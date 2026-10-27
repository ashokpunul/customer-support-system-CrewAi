# CrewAI Customer Support System

A customer-support question answering demo built with CrewAI and Streamlit. For each query, the system produces a direct answer and a web-researched answer, checks the reviewed answer's cited URLs, evaluates answer quality, and asks a human to approve or reject the result.

## What it does

- Runs three CrewAI agents through a sequential workflow.
- Shows a direct, model-generated answer beside a web-researched and reviewed answer.
- Applies a minimum-length guardrail to generated answers.
- Evaluates the researched answer for relevance, groundedness, and completeness.
- Checks up to five cited URLs for reachability and basic text overlap.
- Requires a human decision before writing the final review log.
- Offers both a Streamlit user interface and an interactive terminal interface.

Automated evaluations and URL checks are decision aids. They do not prove that an answer is factually accurate.

## Project flow

The workflow is implemented in `app.py` and is run sequentially:

1. **Customer query** - The user enters a question in the Streamlit app or terminal.
2. **Customer Support Assistant** - Answers from its existing model knowledge without web search.
3. **Web Research and Quality Assistant: initial research** - Uses Serper web search to research the query and provide sources where available.
4. **Web Research and Quality Assistant: verification** - Reviews the initial research, checks important claims, prefers primary sources, and explains uncertainty or limitations.
5. **Web Research and Quality Assistant: evaluation** - Scores the reviewed answer from 1 to 5 for relevance, groundedness, and completeness, then gives an overall recommendation.
6. **Customer Support Record Keeper** - Completes the crew's record-saving stage. The application writes the full record later, after source validation and human review.
7. **Source validation** - Checks up to five unique URLs in the reviewed answer. The checker verifies basic URL safety, attempts to fetch a public HTTP/HTTPS page, and looks for text overlap. A reachable page or text overlap is not proof of a claim's accuracy.
8. **Human review** - The user reviews both answers, evaluation, and source-check results, then approves or rejects the response. Rejection requires correction notes in the Streamlit interface.
9. **Review log** - The application writes the query, answers, evaluation, URL-check results, human decision, and feedback to `output/customer_support_log.txt`.

The answer-quality guardrail rejects answers shorter than 20 characters and allows up to two retries per guarded task.

## Agents and tasks

| Agent | Responsibilities |
|---|---|
| Customer Support Assistant | Produces the direct answer without using web search. |
| Web Research and Quality Assistant | Searches the web, reviews research, verifies claims, and evaluates the reviewed answer. |
| Customer Support Record Keeper | Calls the record-stage tool to indicate the crew has reached the logging step. |

These agents perform five sequential tasks: direct answer, initial web research, factual verification, automated evaluation, and record-stage confirmation. The application handles URL checks, human approval, and final file writing outside the crew.

The configured language model is `openai/gpt-4o-mini`. Web search is provided by `SerperDevTool`.

## Project files

```text
.
├── app.py              # CrewAI agents, tasks, source checks, logging, and CLI entry point
├── streamlit_app.py    # Streamlit interface and workflow orchestration
├── requirement.txt     # Python dependencies
└── output/             # Created automatically; contains the review log
```

## Requirements

- Python
- An OpenAI API key
- A Serper API key
- Internet access for model and search requests and source checks

## Setup

Run these commands from the project directory.

### 1. Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Windows Command Prompt:

```bat
python -m venv .venv
.\.venv\Scripts\activate.bat
```

### 2. Install dependencies

```bash
python -m pip install -r requirement.txt
```

### 3. Configure API credentials

Create a `.env` file in the project root:

```dotenv
OPENAI_API_KEY=your_openai_api_key
SERPER_API_KEY=your_serper_api_key
```

Keep the real credentials private; do not commit them to source control. The application checks for both variables when `app.py` is imported.

## Run the application

### Streamlit interface

```bash
streamlit run streamlit_app.py
```

Enter a query and select **Run Customer Support Agents**. The answers, evaluation, source checks, and human-review controls appear in the page.

### Interactive terminal interface

```bash
python app.py
```

Enter a query when prompted, review the output in the terminal, then enter `y` to approve or `n` to reject. Rejections prompt for correction notes.

## Output and review behavior

- The output directory is created automatically if it does not exist.
- The final review is written to `output/customer_support_log.txt`.
- Each completed review writes to the same file, replacing the previous log.
- In the Streamlit app, the current query's results and review state are held in Streamlit session state.
- Rejecting a response records the decision and feedback; it does not automatically correct or rerun the agents.
- A failed crew run is not approved and does not produce a successful review record.

## Limitations

- Model answers and model-generated scores can be incomplete or incorrect.
- URL validation checks reachability and limited text overlap, not whether a source supports every claim.
- Some sites may block automated requests, redirect, or return content that cannot be checked.
- Source checks are limited to the first five unique URLs found in the reviewed answer.
- The log is a single fixed-path file, so a later review replaces the earlier one.
