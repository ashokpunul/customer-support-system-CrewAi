
import os
import re
import socket
import ipaddress
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from html.parser import HTMLParser

from dotenv import load_dotenv
from crewai import Agent, Crew, LLM, Process, Task
from crewai.tools import tool
from crewai_tools import SerperDevTool


# =========================================================
# 1. ENVIRONMENT SETUP
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "customer_support_log.txt"

load_dotenv(BASE_DIR / ".env")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

if not os.getenv("OPENAI_API_KEY"):
    raise ValueError("OPENAI_API_KEY is missing from .env")

if not os.getenv("SERPER_API_KEY"):
    raise ValueError("SERPER_API_KEY is missing from .env")

llm = LLM(
    model="openai/gpt-4o-mini",
    temperature=0,
)

web_search_tool = SerperDevTool()

user_query = ""

# These references are assigned below.
assistant_task = None
web_search_task = None
verification_task = None
evaluation_task = None


# =========================================================
# 2. BASIC GUARDRAIL
# =========================================================

def validate_answer(result):
    """Reject answers that are too short to be useful."""

    answer = result.raw.strip()

    if len(answer) < 20:
        return False, (
            "The answer is too short. Please provide a useful answer "
            "containing at least 20 characters."
        )

    return True, answer


# =========================================================
# 3. SOURCE VALIDATION
# =========================================================

class PageTextParser(HTMLParser):
    """Extract readable text from an HTML page."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip_tags = {"script", "style", "noscript", "svg"}
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.skip_tags:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.skip_tags:
            self.skip_depth = max(0, self.skip_depth - 1)

    def handle_data(self, data):
        if self.skip_depth == 0 and data.strip():
            self.parts.append(data.strip())


def check_public_http_url(url):
    """
    Basic safety check for public HTTP/HTTPS URLs.

    This is a starter check, not a complete production-grade
    defense against server-side request forgery (SSRF).
    """

    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False, "Only HTTP and HTTPS URLs are allowed"

        if not parsed.hostname:
            return False, "URL hostname is missing"

        if parsed.username or parsed.password:
            return False, "URLs containing credentials are not allowed"

        host = parsed.hostname.lower()

        if host == "localhost" or host.endswith(".localhost"):
            return False, "Localhost URLs are not allowed"

        try:
            ip = ipaddress.ip_address(host)

            if not ip.is_global:
                return False, "Non-public IP addresses are not allowed"

        except ValueError:
            try:
                port = parsed.port or (
                    443 if parsed.scheme == "https" else 80
                )

                addresses = socket.getaddrinfo(host, port)

                if not addresses:
                    return False, "Hostname did not resolve"

                for address in addresses:
                    resolved_ip = ipaddress.ip_address(address[4][0])

                    if not resolved_ip.is_global:
                        return False, (
                            "Hostname resolves to a non-public IP address"
                        )

            except (socket.gaierror, ValueError, OSError):
                return False, "Hostname could not be safely resolved"

        return True, "URL passed basic safety checks"

    except Exception:
        return False, "Invalid URL"


class SafeRedirectHandler(HTTPRedirectHandler):
    """Check redirect destinations before following them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        valid, reason = check_public_http_url(newurl)

        if not valid:
            raise ValueError(f"Unsafe redirect blocked: {reason}")

        return super().redirect_request(
            req, fp, code, msg, headers, newurl
        )


def validate_source(url, answer):
    """Check whether a source is reachable and has text overlap."""

    valid, reason = check_public_http_url(url)

    if not valid:
        return {
            "url": url,
            "status": "UNVERIFIED",
            "details": reason,
        }

    try:
        opener = build_opener(SafeRedirectHandler)

        request = Request(
            url,
            headers={
                "User-Agent": "CustomerSupportSourceChecker/1.0"
            },
        )

        with opener.open(request, timeout=8) as response:
            final_url = response.geturl()

            final_valid, final_reason = check_public_http_url(final_url)

            if not final_valid:
                return {
                    "url": url,
                    "status": "UNVERIFIED",
                    "details": (
                        f"Redirect destination failed safety check: "
                        f"{final_reason}"
                    ),
                }

            content_type = response.headers.get(
                "Content-Type", ""
            ).lower()

            if (
                "text/html" not in content_type
                and "text/plain" not in content_type
            ):
                return {
                    "url": url,
                    "status": "UNVERIFIED",
                    "details": f"Unsupported content type: {content_type}",
                }

            body = response.read(500_000)
            page = body.decode("utf-8", errors="replace")

        if "text/html" in content_type:
            parser = PageTextParser()
            parser.feed(page)
            page_text = " ".join(parser.parts)
        else:
            page_text = page

        if len(page_text.strip()) < 100:
            return {
                "url": url,
                "status": "UNVERIFIED",
                "details": "The page contains too little readable text",
            }

        answer_terms = {
            word.lower()
            for word in re.findall(r"\b[a-zA-Z]{5,}\b", answer)
        }

        page_terms = {
            word.lower()
            for word in re.findall(r"\b[a-zA-Z]{5,}\b", page_text)
        }

        overlap = answer_terms.intersection(page_terms)

        if len(overlap) >= 2:
            return {
                "url": url,
                "status": "REACHABLE; TEXT OVERLAP FOUND",
                "details": (
                    f"{len(overlap)} answer terms appear on the page. "
                    "This does not prove factual accuracy."
                ),
            }

        return {
            "url": url,
            "status": "REACHABLE; RELEVANCE UNCLEAR",
            "details": (
                "The page opened, but text overlap was limited."
            ),
        }

    except Exception as error:
        return {
            "url": url,
            "status": "UNVERIFIED",
            "details": f"Could not validate source: {error}",
        }


def validate_cited_sources(answer):
    """Find and check up to five source URLs in the reviewed answer."""

    found_urls = re.findall(
        r"https?://[^\s<>\]\)\"']+",
        answer,
    )

    urls = []

    for raw_url in found_urls:
        url = raw_url.rstrip(".,;:")

        if url not in urls:
            urls.append(url)

    if not urls:
        return [{
            "url": "(no URLs found)",
            "status": "UNVERIFIED",
            "details": "No source URL was detected in the answer.",
        }]

    return [
        validate_source(url, answer)
        for url in urls[:5]
    ]


# =========================================================
# 4. AGENT 3 TOOL: RECORD-SAVING STAGE
# =========================================================

@tool("save_customer_support_log")
def save_customer_support_log(confirm: str) -> str:
    """Confirm the crew reached the record-saving stage.

    Provide a short confirmation string. The application saves
    the final record after source checks and human review.
    """

    return (
        "Record-saving stage reached. The application will save "
        "the complete record after evaluation and human review."
    )


# =========================================================
# 5. AGENT 1: DIRECT ANSWER
# =========================================================

assistant = Agent(
    role="Customer Support Assistant",
    goal=(
        "Answer the customer's query directly using your own knowledge."
    ),
    backstory=(
        "You are a helpful customer support assistant. "
        "Answer clearly and accurately using your existing knowledge. "
        "Do not claim that you searched the web."
    ),
    llm=llm,
    verbose=True,
)


# =========================================================
# 6. AGENT 2: WEB SEARCH, VERIFICATION AND EVALUATION
# =========================================================

web_search_assistant = Agent(
    role="Web Research and Quality Assistant",
    goal=(
        "Research customer questions, verify important claims, "
        "and evaluate answer quality."
    ),
    backstory=(
        "You research answers using the web search tool. Prefer official "
        "and primary sources. Never invent facts or source URLs. "
        "Identify uncertainty and contradictions. Evaluate answers "
        "strictly using the available evidence."
    ),
    tools=[web_search_tool],
    llm=llm,
    verbose=True,
)


# =========================================================
# 7. AGENT 3: RECORD KEEPER
# =========================================================

entry_agent = Agent(
    role="Customer Support Record Keeper",
    goal=(
        "Complete the record-saving stage after the answers "
        "and evaluation have been generated."
    ),
    backstory=(
        "You maintain customer support records. Use the provided "
        "saving tool. Do not change or invent the other agents' answers. "
        "Human approval is performed by the application afterward."
    ),
    tools=[save_customer_support_log],
    llm=llm,
    verbose=True,
)


# =========================================================
# 8. TASK 1: DIRECT ANSWER
# =========================================================

assistant_task = Task(
    description=(
        "Answer the following customer query using your own knowledge. "
        "Do not use web search.\n\n"
        "Customer query: {query}\n\n"
        "Provide a clear and useful answer. State uncertainty where needed."
    ),
    expected_output="A clear direct answer to the customer's query.",
    agent=assistant,
    guardrail=validate_answer,
    guardrail_max_retries=2,
)


# =========================================================
# 9. TASK 2: WEB RESEARCH
# =========================================================

web_search_task = Task(
    description=(
        "Research the customer's query using the web search tool.\n\n"
        "Customer query: {query}\n\n"
        "Explain the relevant findings and include source URLs when "
        "available. Prefer reliable official sources. Do not invent "
        "facts or URLs."
    ),
    expected_output=(
        "A researched answer with findings and source URLs when available."
    ),
    agent=web_search_assistant,
    guardrail=validate_answer,
    guardrail_max_retries=2,
)


# =========================================================
# 10. TASK 3: FACTUAL VERIFICATION
# =========================================================

verification_task = Task(
    description=(
        "Review the initial web research for factual support.\n\n"
        "Customer query: {query}\n\n"
        "Use the initial web research task provided in the task context. "
        "Check important claims using web search, especially changing "
        "facts, dates, and numbers. Prefer official or primary sources. "
        "Identify contradictions and remove or qualify unsupported claims. "
        "Never invent source URLs.\n\n"
        "Return these sections:\n"
        "VERIFIED ANSWER\n"
        "EVIDENCE AND SOURCES\n"
        "LIMITATIONS\n\n"
        "Clearly explain what you could not verify."
    ),
    expected_output=(
        "A reviewed answer with evidence, source URLs when available, "
        "and clear limitations."
    ),
    agent=web_search_assistant,
    context=[web_search_task],
    guardrail=validate_answer,
    guardrail_max_retries=2,
)


# =========================================================
# 11. TASK 4: AUTOMATED EVALUATION
# =========================================================

evaluation_task = Task(
    description=(
        "Evaluate the quality of the reviewed web answer for the "
        "customer's query.\n\n"
        "Customer query: {query}\n\n"
        "Use the direct answer and reviewed web answer provided through "
        "this task's configured context. Assess the reviewed web answer "
        "using the available research and evidence.\n\n"
        "Score these three dimensions from 1 to 5:\n\n"
        "1. RELEVANCE: Does the answer address the customer's actual query?\n"
        "2. GROUNDEDNESS: Are important factual claims supported by "
        "the available evidence? Do not award a high score just because "
        "URLs are present.\n"
        "3. COMPLETENESS: Does the answer cover the important parts "
        "of the question?\n\n"
        "Scoring guide: 1 = poor, 2 = weak, 3 = acceptable, "
        "4 = good, 5 = excellent.\n\n"
        "Return this format:\n"
        "RELEVANCE: [score]/5 - [reason]\n"
        "GROUNDEDNESS: [score]/5 - [reason]\n"
        "COMPLETENESS: [score]/5 - [reason]\n"
        "OVERALL: [average score]/5\n"
        "ISSUES TO REVIEW: [issues, or None identified]\n"
        "RECOMMENDATION: [PASS or NEEDS HUMAN ATTENTION]\n\n"
        "Calculate the average of the three scores. Recommend "
        "NEEDS HUMAN ATTENTION if any score is below 3, a key claim "
        "appears unsupported, or the evidence is insufficient. "
        "These are model estimates, not proof of accuracy."
    ),
    expected_output=(
        "Three scores with reasons, the average score, issues to review, "
        "and a recommendation."
    ),
    agent=web_search_assistant,
    context=[assistant_task, verification_task],
    guardrail=validate_answer,
    guardrail_max_retries=2,
)


# =========================================================
# 12. TASK 5: RECORD-SAVING STAGE
# =========================================================

entry_task = Task(
    description=(
        "Call the save_customer_support_log tool with a short "
        "confirmation string. The application will save the final "
        "record after source validation and human review. Do not claim "
        "that the response has already been approved."
    ),
    expected_output=(
        "Confirmation that the record-saving stage has been reached."
    ),
    agent=entry_agent,
    context=[
        assistant_task,
        web_search_task,
        verification_task,
        evaluation_task,
    ],
)


# =========================================================
# 13. SEQUENTIAL CREW
# =========================================================

customer_support_crew = Crew(
    agents=[
        assistant,
        web_search_assistant,
        entry_agent,
    ],
    tasks=[
        assistant_task,
        web_search_task,
        verification_task,
        evaluation_task,
        entry_task,
    ],
    process=Process.sequential,
    verbose=True,
)


# =========================================================
# 14. SAVE COMPLETE REVIEW LOG
# =========================================================

def write_customer_support_log(
    source_results,
    human_decision,
    feedback="",
):
    assistant_answer = assistant_task.output.raw.strip()
    initial_web_answer = web_search_task.output.raw.strip()
    verified_answer = verification_task.output.raw.strip()
    evaluation = evaluation_task.output.raw.strip()

    source_lines = []

    for item in source_results:
        source_lines.append(
            f"- URL: {item['url']}\n"
            f"  Status: {item['status']}\n"
            f"  Details: {item['details']}"
        )

    content = (
        "CUSTOMER SUPPORT REVIEW LOG\n"
        "===========================\n\n"
        f"CUSTOMER QUERY:\n{user_query}\n\n"
        f"AGENT 1 - DIRECT ANSWER:\n{assistant_answer}\n\n"
        f"AGENT 2 - INITIAL WEB RESEARCH:\n{initial_web_answer}\n\n"
        f"REVIEWED WEB ANSWER:\n{verified_answer}\n\n"
        f"AUTOMATED EVALUATION:\n{evaluation}\n\n"
        "SOURCE VALIDATION:\n"
        "------------------\n"
        + "\n".join(source_lines)
        + "\n\n"
        "HUMAN REVIEW:\n"
        "-------------\n"
        f"Decision: {human_decision}\n"
        f"Feedback: {feedback or 'None provided'}\n\n"
        "Important: Automated scores and URL checks do not prove "
        "factual accuracy.\n"
        + "=" * 65
        + "\n"
    )

    OUTPUT_FILE.write_text(content, encoding="utf-8")
    return str(OUTPUT_FILE)


# =========================================================
# 15. SOURCE CHECKS AND HUMAN REVIEW
# =========================================================

def perform_human_review():
    verified_answer = verification_task.output.raw.strip()
    evaluation = evaluation_task.output.raw.strip()

    print("\n" + "=" * 65)
    print("STEP 11: AUTOMATED EVALUATION")
    print("=" * 65)
    print(evaluation)

    # Check URLs found in the reviewed answer.
    source_results = validate_cited_sources(verified_answer)

    print("\n" + "=" * 65)
    print("SOURCE VALIDATION RESULTS")
    print("=" * 65)

    for index, item in enumerate(source_results, start=1):
        print(f"\nSource {index}: {item['url']}")
        print(f"Status: {item['status']}")
        print(f"Details: {item['details']}")

    print("\n" + "=" * 65)
    print("HUMAN REVIEW")
    print("=" * 65)

    print("\nCustomer query:")
    print(user_query)

    print("\nAgent 1 - Direct answer:")
    print(assistant_task.output.raw)

    print("\nAgent 2 - Reviewed web answer:")
    print(verified_answer)

    print("\nAutomated evaluation:")
    print(evaluation)

    print(
        "\nReview the answer and evidence before approving it. "
        "The evaluation and source checks are decision aids, "
        "not guarantees of accuracy."
    )

    while True:
        decision = input(
            "\nApprove this response? [y = approve / n = reject]: "
        ).strip().lower()

        if decision in ("y", "n"):
            break

        print("Invalid input. Please enter y or n.")

    if decision == "y":
        human_decision = "APPROVED BY HUMAN REVIEWER"
        feedback = ""
    else:
        human_decision = "REJECTED - NOT APPROVED FOR CUSTOMER USE"
        feedback = input(
            "Enter correction notes for the record: "
        ).strip()

    saved_path = write_customer_support_log(
        source_results=source_results,
        human_decision=human_decision,
        feedback=feedback,
    )

    print(f"\nReview record saved to:\n{saved_path}")

    if decision == "y":
        print("\nFinal status: APPROVED BY HUMAN REVIEWER")
    else:
        print("\nFinal status: REJECTED - CORRECTION REQUIRED")

    return decision


# =========================================================
# 16. MAIN APPLICATION
# =========================================================

def main():
    global user_query

    print("\n" + "=" * 65)
    print("CREWAI CUSTOMER SUPPORT SYSTEM")
    print("3 agents | 5 sequential tasks")
    print("Guardrails | Verification | Automated Evaluation")
    print("Source Checks | Human Review")
    print("=" * 65)

    user_query = input(
        "\nEnter your customer support query: "
    ).strip()

    if not user_query:
        print("No query entered. Please try again.")
        return

    try:
        customer_support_crew.kickoff(
            inputs={"query": user_query}
        )

        decision = perform_human_review()

        if decision == "y":
            print("\nCustomer support workflow completed.")
        else:
            print(
                "\nWorkflow completed, but the response was rejected."
            )

    except Exception as error:
        print("\nThe workflow could not complete.")
        print(f"Error: {error}")
        print(
            "\nCheck the error message, API credentials, network "
            "connection, and CrewAI package versions. A failed run "
            "must never be treated as an approved response."
        )


if __name__ == "__main__":
    main()
