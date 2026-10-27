
import streamlit as st
import app as support


# =====================================================
# PAGE CONFIGURATION
# =====================================================

st.set_page_config(
    page_title="CrewAI Customer Support",
    page_icon="🤖",
    layout="wide",
)

st.title("🤖 CrewAI Customer Support System")
st.caption(
    "Three agents | Sequential workflow | Web research | "
    "Automated evaluation | Human review"
)


# =====================================================
# QUERY INPUT
# =====================================================

st.subheader("Ask a question")

query = st.text_area(
    "Enter your customer support query or task",
    placeholder="Example: What is CrewAI?",
    height=120,
)

run_button = st.button(
    "Run Customer Support Agents",
    type="primary",
    disabled=not query.strip(),
)


# =====================================================
# RUN THE EXISTING CREW
# =====================================================

if run_button:
    # Clear previous result and review state.
    st.session_state.pop("support_result", None)
    st.session_state.pop("support_reviewed", None)

    with st.status(
        "Running the customer support workflow...",
        expanded=True,
    ) as status:
        try:
            support.user_query = query.strip()

            st.write("1. Assistant is preparing a direct answer.")
            st.write("2. Web Search Assistant is researching the query.")
            st.write("3. Verification and automated evaluation are running.")
            st.write("4. Entry Agent is completing the record stage.")

            support.customer_support_crew.kickoff(
                inputs={"query": query.strip()}
            )

            # Save the outputs in Streamlit session state.
            direct_answer = support.assistant_task.output.raw.strip()
            initial_web_answer = (
                support.web_search_task.output.raw.strip()
            )
            verified_answer = (
                support.verification_task.output.raw.strip()
            )
            evaluation = support.evaluation_task.output.raw.strip()

            # Check source URLs found in the reviewed web answer.
            with st.spinner("Checking cited source URLs..."):
                source_results = support.validate_cited_sources(
                    verified_answer
                )

            st.session_state["support_result"] = {
                "query": query.strip(),
                "direct_answer": direct_answer,
                "initial_web_answer": initial_web_answer,
                "verified_answer": verified_answer,
                "evaluation": evaluation,
                "source_results": source_results,
            }

            status.update(
                label="Workflow completed. Review the answers below.",
                state="complete",
                expanded=False,
            )

        except Exception as error:
            status.update(
                label="The workflow failed.",
                state="error",
                expanded=True,
            )
            st.error(f"Error: {error}")
            st.info(
                "Check your .env credentials, internet connection, "
                "and terminal logs. No approval was recorded."
            )


# =====================================================
# DISPLAY BOTH ANSWERS
# =====================================================

result = st.session_state.get("support_result")

if result:
    st.divider()

    st.subheader("Customer query")
    st.info(result["query"])

    # Two answers displayed side by side.
    left, right = st.columns(2)

    with left:
        st.subheader("💬 Assistant Answer")
        st.markdown(result["direct_answer"])

    with right:
        st.subheader("🌐 Web Search Answer")
        st.markdown(result["verified_answer"])

    # Optional expansion for the original web research.
    with st.expander("View initial web research"):
        st.markdown(result["initial_web_answer"])

    # =================================================
    # AUTOMATED EVALUATION
    # =================================================

    st.divider()
    st.subheader("📊 Automated Evaluation")

    st.caption(
        "Model-generated scores are estimates, not proof of factual accuracy."
    )

    st.markdown(result["evaluation"])

    # =================================================
    # SOURCE VALIDATION
    # =================================================

    st.divider()
    st.subheader("🔎 Source Validation")

    for index, item in enumerate(
        result["source_results"], start=1
    ):
        with st.expander(f"Source {index}: {item['url']}"):
            st.write(f"**Status:** {item['status']}")
            st.write(f"**Details:** {item['details']}")

    # =================================================
    # HUMAN REVIEW
    # =================================================

    st.divider()
    st.subheader("🧑‍⚖️ Human Review")

    st.warning(
        "Review both answers, the evidence, and the evaluation before "
        "approving. A reachable URL does not establish that a claim is true."
    )

    feedback = st.text_area(
        "Review notes (required when rejecting)",
        placeholder="Enter correction notes if you reject the response.",
        key="review_feedback",
    )

    approved, rejected = st.columns(2)

    with approved:
        approve_clicked = st.button(
            "✅ Approve Response",
            type="primary",
            disabled=bool(st.session_state.get("support_reviewed")),
        )

    with rejected:
        reject_clicked = st.button(
            "❌ Reject Response",
            disabled=bool(st.session_state.get("support_reviewed")),
        )

    if approve_clicked or reject_clicked:
        is_approved = approve_clicked

        if not is_approved and not feedback.strip():
            st.error("Please provide correction notes before rejecting.")
        else:
            decision = (
                "APPROVED BY HUMAN REVIEWER"
                if is_approved
                else "REJECTED - NOT APPROVED FOR CUSTOMER USE"
            )

            try:
                saved_path = support.write_customer_support_log(
                    source_results=result["source_results"],
                    human_decision=decision,
                    feedback=feedback.strip(),
                )

                st.session_state["support_reviewed"] = decision
                st.session_state["support_log_path"] = saved_path
                st.rerun()

            except Exception as error:
                st.error(f"Could not save the review log: {error}")

    # =================================================
    # REVIEW RESULT
    # =================================================

    review_status = st.session_state.get("support_reviewed")

    if review_status:
        if review_status.startswith("APPROVED"):
            st.success("Response approved by the human reviewer.")
        else:
            st.error("Response rejected. Corrections are required.")

        st.write(
            f"**Review log:** `{st.session_state['support_log_path']}`"
        )

        st.caption(
            "Rejecting records the feedback; it does not automatically "
            "correct or rerun the agents."
        )
