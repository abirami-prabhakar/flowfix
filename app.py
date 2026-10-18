import hmac
import os
from hashlib import sha256
from uuid import uuid4

import streamlit as st
from dotenv import load_dotenv
from pydantic import ValidationError

from fixflow.database import DatabaseError, Store, duplicates, metrics
from fixflow.extraction import InferenceError, extract, possible_urgency
from fixflow.models import Category, Complaint, Location, Status, TRANSITIONS
from fixflow.monitoring import init_monitoring
from fixflow.seed import seed

load_dotenv()
st.set_page_config(page_title="FixFlow · Maintenance made clear", page_icon="🛠️", layout="wide")


@st.cache_resource
def configure_monitoring():
    return init_monitoring()


def manager_access():
    password = os.getenv("MANAGER_PASSWORD", "")
    if not password:
        st.info("Manager access is disabled. Set MANAGER_PASSWORD in .env to enable it.")
        return False
    # Bind the session to the current configured password without retaining it.
    token = sha256(password.encode()).hexdigest()
    if hmac.compare_digest(st.session_state.get("manager_token", ""), token):
        if st.sidebar.button("Sign out"):
            st.session_state.pop("manager_token", None)
            st.rerun()
        return True
    st.caption("Sign in to view resident requests, assignments, and activity history.")

    def sign_in():
        entered = st.session_state.pop("manager_password_input", "")
        valid = hmac.compare_digest(entered.encode(), password.encode())
        if valid:
            st.session_state.manager_token = token
        st.session_state.manager_login_failed = not valid

    with st.form("login"):
        st.text_input("Manager password", type="password", max_chars=256,
                      key="manager_password_input")
        st.form_submit_button("Sign in", on_click=sign_in)
    if st.session_state.get("manager_login_failed"):
        st.error("Incorrect password.")
    return False


def urgency_warning():
    st.error("Possible urgent issue: contact the manager promptly. This app cannot assess safety or replace emergency assistance.")


def show_draft(draft, *, show_urgency=True):
    result = draft.extraction
    if draft.source == "demo":
        st.warning("SAMPLE RESULT · Deterministic demo extraction; no AI model was called.")
    else:
        st.caption(f"AI-generated suggestion · {draft.latency_ms / 1000:.2f}s · Review before saving")
        st.text(f"Model: {draft.model}")
    if draft.urgent_review and show_urgency:
        urgency_warning()
    indicators = st.columns(2)
    indicators[0].badge(result.category.value, color="blue")
    priority_color = {"Low": "gray", "Normal": "blue", "High": "orange", "Urgent review": "red"}
    indicators[1].badge("Suggested priority: " + result.suggested_priority.value,
                        color=priority_color[result.suggested_priority.value])
    st.subheader("Request summary")
    # Resident/model text must remain literal: Markdown can otherwise load remote images.
    st.text(result.issue_summary)
    locations = st.columns(3)
    for column, (name, value) in zip(locations, result.location.model_dump().items()):
        column.caption({"building": "Building", "floor": "Floor", "area": "Room / common area"}[name])
        column.text(value or "Not provided")
    if result.missing_information:
        st.warning("Missing information needs review. Unknown details remain explicitly flagged.")
        st.text("Missing information: " + ", ".join(result.missing_information))
    if result.follow_up_questions:
        st.markdown("**Questions to clarify**")
        for index, question in enumerate(result.follow_up_questions, start=1):
            st.text(f"{index}. {question}")
    st.caption("Suggestions may be inaccurate. No technical diagnosis or repair completion is implied.")


def new_request(store):
    st.title("Let’s get it fixed.")
    st.write("Describe what you noticed. Review the structured request before sending it to your manager.")
    mode = st.radio("Extraction", ["demo", "ollama"], horizontal=True,
                    index=1 if os.getenv("FIXFLOW_MODE") == "ollama" else 0,
                    format_func=lambda x: "Gemma via Ollama" if x == "ollama" else "Demo · sample results")
    urgent_notice_shown = False
    with st.form("complaint"):
        description = st.text_area("What needs attention?", max_chars=3000, height=140,
                                   placeholder="The bathroom tap keeps dripping even when it is closed.",
                                   key="complaint_description")
        cols = st.columns(3)
        building = cols[0].text_input("Building", max_chars=120, key="complaint_building")
        floor = cols[1].text_input("Floor", max_chars=120, help="Use 0 or Ground for the ground floor.", key="complaint_floor")
        area = cols[2].text_input("Room / common area", max_chars=120, key="complaint_area")
        st.caption("Leave unknown location fields blank. Follow-up answers can be added here or to the description, then extracted again.")
        if st.form_submit_button("Review request", type="primary"):
            st.session_state.pop("draft", None)
            st.session_state.pop("submission_key", None)
            try:
                complaint = Complaint(description=description, location=Location(building=building, floor=floor, area=area))
                if possible_urgency(complaint.description):
                    urgency_warning()
                    urgent_notice_shown = True
                with st.spinner("Structuring your request…"):
                    st.session_state.draft = extract(complaint, mode)
                st.session_state.submission_key = str(uuid4())
            except ValidationError:
                st.error("Enter a description with 8–3000 characters and location fields of at most 120 characters.")
            except InferenceError as exc:
                st.error(str(exc))
    draft = st.session_state.get("draft")
    if draft is None:
        return
    st.divider()
    show_draft(draft, show_urgency=not urgent_notice_shown)
    # Streamlit form edits reach Python only when submitted; the visible review is the saved draft.
    st.caption("Saving sends the reviewed version shown above. Submit Review request again to include any edits.")
    # Only disclose that a match exists, never another resident's ticket data.
    matches = duplicates(draft, store.tickets())
    if matches:
        st.warning("A similar unresolved request may already exist at this location. Your manager will review possible duplicates.")
    if draft.extraction.follow_up_questions:
        st.info("To answer, update the location fields or add details to the description above, then select Review request again.")
    # A fresh extraction must get fresh consent, even after a previous review was checked.
    confirm = st.checkbox("I reviewed the suggestions and any missing information.",
                          key="review-" + st.session_state.submission_key)
    if st.button("Save reviewed ticket", type="primary", disabled=not confirm):
        ticket_id = store.save(draft, st.session_state.submission_key)
        st.session_state.pop("draft", None)
        st.success(f"Request FF-{ticket_id:04d} saved. Your manager can review and track it.")


def dashboard(store):
    st.title("Manager Dashboard")
    if not manager_access():
        return
    if st.button("Add synthetic demo tickets"):
        seed(store)
        st.success("Synthetic demo tickets added. Seeding is safe to repeat.")
    tickets = store.tickets()
    stats = metrics(tickets)
    columns = st.columns(3)
    columns[0].metric("Total requests", len(tickets))
    columns[1].metric("Unresolved", sum(t["status"] not in {"Resolved", "Closed"} for t in tickets))
    avg = stats["average_resolution_hours"]
    columns[2].metric("Average resolution", "No resolved tickets" if avg is None else f"{avg:.1f} hours")
    st.caption(f"Resolution average uses {stats['resolved_sample_count']} currently resolved/closed tickets. Demo tickets are included.")
    with st.expander("Counts by status and category"):
        st.write("Status", stats["status"])
        st.write("Category", stats["category"])
    cols = st.columns(3)
    status = cols[0].selectbox("Status", ["All"] + [s.value for s in Status])
    category = cols[1].selectbox("Category", ["All"] + [c.value for c in Category])
    building = cols[2].selectbox("Building", ["All"] + sorted({t["draft"].complaint.location.building for t in tickets if t["draft"].complaint.location.building}))
    visible = [t for t in tickets if (status == "All" or t["status"] == status)
               and (category == "All" or t["draft"].extraction.category.value == category)
               and (building == "All" or t["draft"].complaint.location.building == building)]
    if not visible:
        st.info("No tickets match these filters.")
        return
    st.caption(f"Showing {len(visible)} of {len(tickets)} requests. Select a ticket below to manage it.")
    st.dataframe([{"Ticket": f"FF-{t['id']:04d}", "Issue": t["draft"].extraction.issue_summary,
                   "Status": t["status"], "Priority": t["draft"].extraction.suggested_priority.value,
                   "Location": " / ".join(v or "Unknown" for v in t["draft"].complaint.location.model_dump().values()),
                   "Category": t["draft"].extraction.category.value, "Staff": t["staff"],
                   "Source": t["draft"].source} for t in visible], hide_index=True, width="stretch")
    ticket_id = st.selectbox("Open ticket details", [t["id"] for t in visible], format_func=lambda i: f"FF-{i:04d}")
    ticket = store.get(ticket_id)
    if ticket is None:
        st.warning("This ticket is no longer available. Refresh the dashboard.")
        return
    st.divider()
    st.header(f"Ticket Details · FF-{ticket_id:04d}")
    st.badge("Status: " + ticket["status"],
             color={"New": "blue", "Assigned": "violet", "In progress": "orange", "Resolved": "green", "Closed": "gray"}[ticket["status"]])
    show_draft(ticket["draft"])
    with st.expander("Original complaint"):
        st.text(ticket["draft"].complaint.description)
    st.caption(f"Created {ticket['created_at']} · Updated {ticket['updated_at']} (UTC)")
    matches = duplicates(ticket["draft"], [t for t in tickets if t["id"] != ticket_id])
    if matches:
        st.warning("Possible duplicates: " + ", ".join(f"FF-{i:04d} (similarity {score:.0%})" for i, score in matches))
    old = Status(ticket["status"])
    options = [old.value] + sorted(s.value for s in TRANSITIONS[old])
    with st.form(f"manage-{ticket_id}-{ticket['updated_at']}"):
        staff = st.text_input("Assigned staff", value=ticket["staff"], max_chars=120)
        next_status = st.selectbox("Update status", options)
        if st.form_submit_button("Save changes", type="primary"):
            try:
                store.update(ticket_id, next_status, staff, ticket["status"],
                             expected_updated_at=ticket["updated_at"])
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    st.subheader("Activity history")
    st.dataframe(store.history(ticket_id), hide_index=True, width="stretch")


def main():
    monitoring_warning = configure_monitoring()
    if monitoring_warning:
        st.warning(monitoring_warning)
    st.sidebar.title("🛠️ FixFlow")
    st.sidebar.caption("Small spaces. Clear requests.")
    page = st.sidebar.radio("Navigation", ["New Request", "Manager Dashboard"])
    if os.getenv("PUBLIC_DEMO", "false").lower() == "true":
        st.info("Synthetic public demo. Do not submit real resident information.")
    store = Store()
    if page == "New Request":
        new_request(store)
    else:
        dashboard(store)


try:
    main()
except DatabaseError as exc:
    st.error(str(exc))
