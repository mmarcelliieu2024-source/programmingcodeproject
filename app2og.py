
# app2.py — Main Streamlit app (router + pages) WITH GOOGLE OAUTH
import os
from pathlib import Path
from datetime import datetime
import streamlit as st
import pandas as pd

# MUST be the first Streamlit call
st.set_page_config(page_title="SpendSense · Auth + SQLite + Chatbot",
                   page_icon="🧠", layout="wide")

import db  # SQLite + CRUD layer
from auth_ui2 import (
    show_login,
    show_signup,
    show_change_password,
    logout,
    is_authenticated,
    handle_google_oauth_callback,  # NEW
)

# --- Rerun helper (compatible with old Streamlit versions) ---
def _rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()  # older versions
        except Exception:
            pass

# =========================
# Utilities
# =========================
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

def _save_uploaded_file(file, prefix: str) -> str:
    """Save an UploadedFile/camera_input and return local path (str)."""
    ext = ".png"
    name = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
    dest = UPLOAD_DIR / name
    dest.write_bytes(file.getbuffer())
    return str(dest)

def _safe_link_button(label: str, url: str, key: str | None = None):
    """Use link_button if available; otherwise fallback to a normal link."""
    try:
        st.link_button(label, url, key=key)
    except Exception:
        st.markdown(f"[{label}]({url})")

# =========================
# API Key helpers (AI)
# =========================
def _get_api_key() -> str:
    # 1) local app_secrets.py
    try:
        from app_secrets import OPENAI_API_KEY as _KEY
        if _KEY:
            return _KEY
    except Exception:
        pass
    # 2) st.secrets (.streamlit/secrets.toml)
    try:
        return st.secrets["OPENAI_API_KEY"]
    except Exception:
        pass
    # 3) environment var
    return os.getenv("OPENAI_API_KEY", "")

def llm_available() -> bool:
    return bool(_get_api_key())

def call_llm(messages, model="gpt-4o-mini", temperature=0.2, fallback=""):
    """
    If no API key:
      - fallback == ""       → return "" (silent)
      - fallback == "local"  → return a tiny demo summary
    """
    api_key = _get_api_key()
    if not api_key:
        if fallback == "local":
            last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            return f"(demo) {last_user[:120]}..."
        return ""  # silent

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        return resp.choices[0].message.content
    except Exception as e:
        # No tech noise for the end user
        return f"(AI not available: {e})"

# =========================
# Chatbot UI (Economic / Sustainable advisor)
# =========================
def _user_metrics(user: str):  # Changed parameter name
    """Collect SpendSense metrics for context."""
    pos = db.list_user_items(user, status="positive", order_by="-created_at")
    neg = db.list_user_items(user, status="negative", order_by="-created_at")

    total_savings = float(sum((x.get("savings") or 0) for x in pos))
    total_spent   = float(sum((x.get("price")   or 0) for x in neg))
    co2_pos = float(sum((x.get("co2_estimate") or 0) for x in pos))
    co2_neg = float(sum((x.get("co2_estimate") or 0) for x in neg))

    return {
        "pos": pos, "neg": neg,
        "total_savings": total_savings,
        "total_spent": total_spent,
        "co2_pos": co2_pos,
        "co2_neg": co2_neg,
        "has_data": (len(pos) + len(neg)) > 0,
    }

def _local_econ_analyze(m, concern_text: str) -> str:
    tips = []
    if m["has_data"]:
        tips.append(f"- Your positive actions saved **€{m['total_savings']:.2f}**. Double down on second-hand and 'no-buy' weeks.")
        if m["total_spent"] > 0:
            cap = max(10.0, round(0.2 * m["total_spent"] / max(1, len(m['neg'])), 2))
            tips.append(f"- Set a **weekly discretionary cap** around **€{cap:.2f}** based on your recent originals.")
        tips.append("- Use a 48-hour cooling-off rule before any new purchase.")
    else:
        tips.append("- I can't see SpendSense data yet. Start logging items to personalize your plan.")
        tips.append("- Quick wins: track every clothing purchase, prefer second-hand for basics, and limit impulse buys.")

    if concern_text.strip():
        tips.append(f"- Regarding your note: **{concern_text.strip()}** → break it into micro-budgets and define a monthly target you can stick to.")
    tips.append("- Measure: weekly spend vs. cap, % second-hand, and € saved vs. last month.")
    return "### Economic analysis\n" + "\n".join(tips)

def _local_econ_plan(m, concern_text: str) -> str:
    target_save = max(20.0, round(0.1 * (m["total_spent"] or 100), 2))
    plan = [
        f"**Goal this week:** save **€{target_save:.2f}** and avoid one impulse buy.",
        "Day 1: List 3 upcoming needs; postpone wants for 48h.",
        "Day 2: Set a weekly budget cap; split groceries vs. discretionary.",
        "Day 3: Compare 2 second-hand options for your next item.",
        "Day 4: Unsubscribe from 3 promo emails; mute push notifications.",
        "Day 5: No-spend day. Use a wish-list instead of buying.",
        "Day 6: Review progress; move saved money to a separate space.",
        "Day 7: Retrospective: keep 1 habit, drop 1 friction point.",
    ]
    if concern_text.strip():
        plan.insert(1, f"Personal note anchor: **{concern_text.strip()}** → translate into a concrete € target.")
    return "### 7-day Economic action plan\n" + "\n".join([f"- {p}" for p in plan])

def _local_sust_analyze(m, concern_text: str) -> str:
    tips = []
    if m["has_data"]:
        tips.append(f"- You've avoided **{m['co2_pos']:.1f} kg CO₂** via positive actions. Great baseline.")
        tips.append("- Prioritize natural or recycled materials; check labels for blends with lower impact.")
        tips.append("- Keep using second-hand for high-impact categories (coats, denim, shoes).")
    else:
        tips.append("- I can't see SpendSense data yet. Start logging items to personalize your footprint.")
        tips.append("- Quick wins: buy fewer but better, favor second-hand, and repair before replacing.")

    if concern_text.strip():
        tips.append(f"- About your note: **{concern_text.strip()}** → set a monthly CO₂ budget and track it per item.")
    tips.append("- Measure: items repaired, % second-hand, and estimated CO₂ per item.")
    return "### Sustainable analysis\n" + "\n".join(tips)

def _local_sust_plan(m, concern_text: str) -> str:
    plan = [
        "Goal this week: cut estimated clothing footprint by **30%**.",
        "Day 1: Audit your closet; list 2 items to repair instead of replacing.",
        "Day 2: Choose second-hand for the next purchase; compare 3 listings.",
        "Day 3: Prefer lower-impact fibers (linen, cotton) over polyester/nylon when feasible.",
        "Day 4: One care optimization: wash cold, line-dry. Log it.",
        "Day 5: No-buy day; extend the use of what you own.",
        "Day 6: Swap with a friend; document 1 item exchanged.",
        "Day 7: Review footprint and set next week's CO₂ target.",
    ]
    if concern_text.strip():
        plan.insert(1, f"Personal note anchor: **{concern_text.strip()}** → define a measurable CO₂ target.")
    return "### 7-day Sustainable action plan\n" + "\n".join([f"- {p}" for p in plan])

def _render_keywords_list(text: str):
    # Show keywords as simple chips/bullets
    kws = [k.strip() for k in text.replace("\n", ",").split(",") if k.strip()]
    if not kws:
        st.write(text)
        return
    cols = st.columns(min(6, max(2, (len(kws) + 5)//6)))
    for i, kw in enumerate(kws):
        with cols[i % len(cols)]:
            st.markdown(f"- {kw}")

def chatbot_page():
    st.header("🧠 SpendSense Advisor")

    # --- Pull context ---
    username = st.session_state.get("username", "anon")
    m = _user_metrics(username)

    # --- Quick summary (always visible) ---
    with st.expander("Your current data (quick summary)", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("💚 Total savings", f"€{m['total_savings']:.2f}")
        c2.metric("🟥 Original spend", f"€{m['total_spent']:.2f}")
        c3.metric("🌿 CO₂ avoided", f"{m['co2_pos']:.1f} kg")
        c4.metric("🔥 CO₂ generated", f"{m['co2_neg']:.1f} kg")

    # --- Controls ---
    top1, top2, top3 = st.columns([1, 1, 1])
    with top1:
        domain = st.radio("Domain", ["Economic", "Sustainable"], horizontal=True)
    with top2:
        tone = st.selectbox("Tone", ["Precise", "Creative"], index=0)
    with top3:
        st.caption("The assistant uses your SpendSense data when available.")

    temperature = 0.2 if tone == "Precise" else 0.8

    user_text = st.text_area(
        "What concerns you about your consumption?",
        placeholder=(
            "Examples:\n"
            "- I overspend on clothes; I want to reduce €100/month.\n"
            "- Greener alternatives for basics (e.g., T-shirts).\n"
            "- How can I start a simple student budget?"
        ),
        height=140
    )

    # Buttons (includes an explicit "Send concern")
    b1, b2, b3, b4 = st.columns([1, 1, 1, 1])
    do_analyze   = b1.button("Analyze", type="primary")
    do_plan      = b2.button("Action Plan")
    do_keywords  = b3.button("Keywords")
    do_send      = b4.button("Send concern")

    # If "Send concern" without text, warn and stop
    if do_send and not (user_text or "").strip():
        st.warning("Please write your concern before sending.")
        return

    # If nothing was pressed, show hint and stop
    if not (do_analyze or do_plan or do_keywords or do_send):
        if not m["has_data"]:
            st.info("No SpendSense data yet. I'll still give starter advice when you press a button.")
        else:
            st.caption("Tip: press a button to get tailored advice using your saved actions and purchases.")
        return

    # Treat "Send concern" same as "Analyze"
    trigger_analyze = do_analyze or do_send

    # --- Build LLM messages (non-trivial system context) ---
    system_context = (
        "You are an in-app advisor for a Streamlit product called SpendSense. "
        "Purpose: help users take practical decisions about **economic** (budgeting, saving, purchase timing) "
        "or **sustainable** (CO₂ footprint, materials, repair, second-hand) consumption. "
        "You must use the structured context provided (user savings/spend/CO₂ totals) if available. "
        "Always be concise, propose measurable steps, and prefer lists with concrete actions. "
        "If no user data is available, explicitly state that and provide starter guidance anyway. "
        "Never ask for personal data beyond what is needed."
    )
    user_context = (
        f"Context → savings €{m['total_savings']:.2f} in {len(m['pos'])} positive actions; "
        f"original spend €{m['total_spent']:.2f} in {len(m['neg'])} purchases; "
        f"CO2 avoided {m['co2_pos']:.2f} kg; CO2 generated {m['co2_neg']:.2f} kg. "
        f"Has data: {m['has_data']}. Domain: {domain}."
    )
    user_note = (user_text or "").strip()

    def _messages_for(task: str):
        msgs = [{"role": "system", "content": system_context},
                {"role": "user", "content": user_context}]
        if not user_note:
            if task == "analyze":
                prompt = ("No user note provided. Using context only, give exactly 3 tailored recommendations "
                          f"for the **{domain}** domain, each with a one-line rationale and how to measure it this week.")
            elif task == "plan":
                prompt = (f"No user note provided. Build a compact 7-day **{domain}** action plan with daily steps "
                          "and one quantifiable weekly goal.")
            else:
                prompt = (f"No user note provided. Return 10–12 short **{domain}** keywords, comma-separated, "
                          "based on the context.")
            msgs.append({"role": "user", "content": prompt})
            return msgs

        if task == "analyze":
            prompt = (f"User note: {user_note}\n"
                      f"Task: give 4–6 **{domain}** recommendations with specific steps and metrics.")
        elif task == "plan":
            prompt = (f"User note: {user_note}\n"
                      f"Task: produce a 7-day **{domain}** action plan with a weekly goal and daily steps.")
        else:
            prompt = (f"User note: {user_note}\n"
                      f"Task: return only 10–12 **{domain}** keywords, comma-separated.")
        msgs.append({"role": "user", "content": prompt})
        return msgs

    # --- If API key exists → use LLM. Else → local heuristics fallback ---
    if llm_available():
        with st.spinner("Thinking…"):
            if trigger_analyze:
                out = call_llm(_messages_for("analyze"), temperature=temperature)
                st.subheader("🔎 Analysis")
                st.markdown(out or "_No answer_")
            elif do_plan:
                out = call_llm(_messages_for("plan"), temperature=temperature)
                st.subheader("🧭 Action Plan")
                st.markdown(out or "_No answer_")
            else:  # keywords
                out = call_llm(_messages_for("keywords"), temperature=temperature)
                st.subheader("🏷️ Keywords")
                if out:
                    _render_keywords_list(out)
                else:
                    st.write("_No answer_")
    else:
        if trigger_analyze:
            st.subheader("🔎 Analysis (offline)")
            if domain == "Economic":
                st.markdown(_local_econ_analyze(m, user_text))
            else:
                st.markdown(_local_sust_analyze(m, user_text))
        elif do_plan:
            st.subheader("🧭 Action Plan (offline)")
            if domain == "Economic":
                st.markdown(_local_econ_plan(m, user_text))
            else:
                st.markdown(_local_sust_plan(m, user_text))
        else:
            st.subheader("🏷️ Keywords (offline)")
            if domain == "Economic":
                _render_keywords_list("budget cap, no-spend day, envelope method, price anchor, 48-hour rule, second-hand, wishlist, impulse control, savings goal, discretionary spend, cash stuffing, micro-budget")
            else:
                _render_keywords_list("second-hand, repair, cold wash, line dry, natural fibers, footprint, swap, durable, circularity, recycling, low-impact dye, capsule wardrobe")

# =========================
# CO2 helpers (Base 44-inspired)
# =========================
_MATERIAL_FACTORS = {
    "cotton": 2.1, "polyester": 5.5, "wool": 10.4, "linen": 1.7,
    "silk": 11.0, "nylon": 6.0, "mixed": 4.0, "leather": 17.0, "other": 4.0
}
_CATEGORY_WEIGHTS = {
    "shirt": 0.2, "pants": 0.5, "dress": 0.4, "jacket": 0.8, "coat": 1.2,
    "shoes": 0.6, "accessories": 0.1, "other": 0.3
}
# NOTE: Keys in English to match "Country of origin"
_TRANSPORT_FACTORS = {
    "Spain": 0.2, "Portugal": 0.3, "France": 0.4, "Italy": 0.4,
    "China": 1.5, "India": 1.4, "Bangladesh": 1.6, "Vietnam": 1.5, "Turkey": 0.8
}
def estimate_co2(material: str, category: str, origin: str):
    mf = _MATERIAL_FACTORS.get(material or "other", _MATERIAL_FACTORS["other"])
    w  = _CATEGORY_WEIGHTS.get(category or "other", _CATEGORY_WEIGHTS["other"])
    tf = _TRANSPORT_FACTORS.get(origin or "", 1.0)
    co2 = mf * w * tf
    level = "low"
    if co2 > 10:
        level = "high"
    elif co2 > 5:
        level = "medium"
    return round(co2, 3), level

# =========================
# Page: Upload (image + label + edit)
# =========================
def upload_page():
    st.header("📤 Upload item")

    state = st.session_state
    state.setdefault("upl_step", "choose")
    state.setdefault("upl_product_path", None)
    state.setdefault("upl_label_path", None)
    state.setdefault("upl_data", {
        "brand": "Brand to identify",
        "price": 50.0,
        "origin": "Unknown",
        "material": "other",
        "category": "other",
        "title": "Item",
        "color": "",
        "confidence": 0.0
    })

    if state.upl_step == "choose":
        st.subheader("1) Choose product image")
        colA, colB = st.columns(2)
        with colA:
            st.write("**Gallery**")
            f = st.file_uploader("Select an image", type=["png", "jpg", "jpeg"],
                                 key="file_uploader_gallery")
            if f is not None:
                path = _save_uploaded_file(f, "product")
                state.upl_product_path = path
                state.upl_step = "label"
                st.success("✅ Image uploaded.")
                _rerun()
        with colB:
            st.write("**Camera**")
            cam = st.camera_input("Take a photo", key="camera_input")
            if cam is not None:
                path = _save_uploaded_file(cam, "product")
                state.upl_product_path = path
                state.upl_step = "label"
                st.success("✅ Photo captured.")
                _rerun()
        return

    if state.upl_step == "label":
        st.subheader("2) Upload label photo (optional)")
        if state.upl_product_path:
            st.image(state.upl_product_path, caption="Product image", use_container_width=True)

        lab = st.file_uploader("Label photo (optional)", type=["png", "jpg", "jpeg"],
                               key="label_uploader")
        c1, c2 = st.columns(2)
        with c1:
            if lab is not None:
                path = _save_uploaded_file(lab, "label")
                state.upl_label_path = path
                st.info("Label uploaded. You can try AI autocomplete (experimental).")
        with c2:
            if st.button("Skip without label", key="skip_label_btn"):
                state.upl_step = "review"
                _rerun()

        # AI autocomplete (experimental, no OCR)
        with st.expander("🧠 AI autocomplete (no OCR)"):
            if not llm_available():
                st.caption("AI demo mode: it will activate when you set your API key.")
            hint = st.text_input("Short hint (e.g., 'Zara cotton T-shirt 19.99 made in Spain, white')",
                                 key="ia_hint")
            if st.button("Suggest data", key="ia_propose"):
                prompt = [
                    {"role": "system",
                     "content": "Return compact JSON with brand, price, origin, material, category, title, color (if known) and confidence (0-1)."},
                    {"role": "user", "content": f"Hint: {hint}"}
                ]
                reply = call_llm(prompt, temperature=0.2, fallback="local")
                st.write("AI suggestion:")
                st.code(reply)
                st.info("Review/edit manually below.")

        if st.button("Continue", key="continue_to_review"):
            state.upl_step = "review"
            _rerun()
        return

    if state.upl_step == "review":
        st.subheader("3) Review and edit information")
        colL, colR = st.columns([1, 1])
        with colL:
            if state.upl_product_path:
                st.image(state.upl_product_path, caption="Product", use_container_width=True)
            if state.upl_label_path:
                st.image(state.upl_label_path, caption="Label", use_container_width=True)
        with colR:
            data = state.upl_data
            data["brand"]   = st.text_input("Brand", value=data["brand"])
            data["title"]   = st.text_input("Title/Description", value=data["title"])
            data["price"]   = st.number_input("Price (€)", value=float(data["price"]), step=0.5, min_value=0.0)
            data["origin"]  = st.text_input("Country of origin", value=data["origin"])
            data["color"]   = st.text_input("Main color (optional)", value=data.get("color", ""))

            col1, col2 = st.columns(2)
            with col1:
                data["material"] = st.selectbox(
                    "Material",
                    ["cotton","polyester","wool","linen","silk","nylon","mixed","leather","other"],
                    index=["cotton","polyester","wool","linen","silk","nylon","mixed","leather","other"].index(data["material"])
                )
            with col2:
                data["category"] = st.selectbox(
                    "Category",
                    ["shirt","pants","dress","jacket","coat","shoes","accessories","other"],
                    index=["shirt","pants","dress","jacket","coat","shoes","accessories","other"].index(data["category"])
                )

            co2, lvl = estimate_co2(data["material"], data["category"], data["origin"])
            st.info(f"🌿 CO₂ estimate: **{co2} kg** (level: **{lvl}**)")

            if st.button("✅ Confirm and continue", key="confirm_review"):
                username = st.session_state.get("username", "anon")
                item_id = db.create_item(
                    created_by=username,
                    source="original",
                    title=data["title"],
                    brand=data["brand"],
                    price=float(data["price"]),
                    origin=data["origin"],
                    material=data["material"],
                    category=data["category"],
                    image_path=state.upl_product_path,
                    label_image_path=state.upl_label_path,
                    co2_estimate=co2,
                    co2_level=lvl,
                    status="in_cart",
                    action_type="none",
                    color=(data.get("color") or None),
                    confidence=float(data.get("confidence") or 0.0),
                )
                st.success("Item saved to your smart cart.")
                # Prepare jump to Alternatives or Cart
                st.session_state["alt_last_item_id"] = item_id
                st.session_state.upl_step = "choose"
                st.session_state.upl_product_path = None
                st.session_state.upl_label_path = None
                st.session_state.upl_data = {
                    "brand": "Brand to identify", "price": 50.0, "origin": "Unknown",
                    "material": "other", "category": "other", "title": "Item", "color": "", "confidence": 0.0
                }
                _rerun()

        last_id = st.session_state.get("alt_last_item_id")
        if last_id:
            st.divider()
            st.subheader("View second-hand alternatives?")
            c1, c2 = st.columns([1,1])
            with c1:
                if st.button("Yes, find alternatives", key="go_alt_yes"):
                    st.session_state["go_alt_item_id"] = last_id
                    st.session_state["alt_last_item_id"] = None
                    _rerun()
            with c2:
                if st.button("No, go to cart", key="go_alt_no"):
                    st.session_state["alt_last_item_id"] = None
                    st.session_state["nav_radio"] = "Cart"
                    _rerun()

        go_alt_id = st.session_state.get("go_alt_item_id")
        if go_alt_id:
            alternatives_page(go_alt_id)

# =========================
# Page: Alternatives (second-hand)
# =========================
def _mk_search_links(brand, category, color):
    term = " ".join([x for x in [brand, category, color] if x]).strip()
    q = term.replace(" ", "+")
    return {
        "Vinted": f"https://www.vinted.es/catalog?search_text={q}",
        "Wallapop": f"https://es.wallapop.com/app/search?keywords={q}",
        "Micolet": f"https://www.micolet.com/buscar/{term.replace(' ','-')}",
        "Vestiaire": f"https://www.vestiairecollective.com/search/?q={q}"
    }

def alternatives_page(item_id: int):
    st.subheader("🛍️ Second-hand alternatives")
    item = db.get_item(item_id)
    if not item:
        st.warning("Selected item not found.")
        return

    cols = st.columns([1, 2])
    with cols[0]:
        if item.get("image_path"):
            st.image(item["image_path"], caption=item["title"], use_container_width=True)
        st.write(f"**{item['brand']}** · €{item['price']:.2f}")
        st.caption(f"CO₂ estimate: {item['co2_estimate']} kg ({item['co2_level']})")
    with cols[1]:
        st.info("Click to open marketplace searches and add similar alternatives to your cart.")

        links = _mk_search_links(item.get("brand",""), item.get("category",""), item.get("color",""))
        c1,c2,c3,c4 = st.columns(4)
        _safe_link_button("Vinted", links["Vinted"], key="lk_vinted")
        _safe_link_button("Wallapop", links["Wallapop"], key="lk_wallapop")
        _safe_link_button("Micolet", links["Micolet"], key="lk_micolet")
        _safe_link_button("Vestiaire", links["Vestiaire"], key="lk_vestiaire")

        st.divider()
        st.write("**Add quick estimated alternative**:")
        colA, colB, colC = st.columns([2,1,1])
        with colA:
            alt_title = st.text_input("Title",
                value=f"{item['brand']} {item['category']} - second-hand",
                key=f"alt_title_{item_id}")
        with colB:
            factor = st.slider("Price %", 30, 80, 60,
                               help="Percentage of original price",
                               key=f"alt_pct_{item_id}")
        with colC:
            add_alt = st.button("➕ Add", key=f"add_alt_{item_id}")

        if add_alt:
            alt_price = round(item["price"] * (factor/100), 2)
            _id = db.create_item(
                created_by=st.session_state.get("username","anon"),
                source="second_hand",
                title=alt_title, brand=item.get("brand",""),
                price=alt_price, origin=item.get("origin","Unknown"),
                material=item.get("material","other"), category=item.get("category","other"),
                image_path=item.get("image_path"), label_image_path=None,
                co2_estimate=round(item.get("co2_estimate",0)*0.3,3), co2_level="low",
                status="in_cart", action_type="none",
                color=item.get("color"), confidence=0.0,
            )
            st.success(f"Alternative added to cart (id={_id}).")

        st.divider()
        go_cart = st.button("Go to smart cart", key="go_cart_btn")
        if go_cart:
            st.session_state["nav_radio"] = "Cart"
            _rerun()

# =========================
# Page: Smart Cart
# =========================
def smart_cart_page():
    st.header("🛒 Smart Shopping Cart")

    username = st.session_state.get("username","anon")
    items = db.list_user_items(username, status="in_cart", order_by="-created_at")

    if not items:
        st.info("Your cart is empty. Upload an image in **Upload item**.")
        return

    for it in items:
        with st.container():
            cols = st.columns([1, 3, 2])
            with cols[0]:
                if it.get("image_path"):
                    st.image(it["image_path"], use_container_width=True)
            with cols[1]:
                st.markdown(f"**{it['title']}**")
                st.caption(f"{it.get('brand','')} · €{it['price']:.2f}")
                st.caption(f"Material: {it.get('material','?')} · CO₂: {it.get('co2_estimate',0)} kg ({it.get('co2_level','')})")
            with cols[2]:
                st.write("Action")
                act = st.radio(
                    "Choose action",
                    ["—", "Bought the original", "Saved the money", "Bought second-hand"],
                    key=f"act_{it['id']}",
                    label_visibility="collapsed",
                )
                if act == "Bought the original":
                    if st.button("Confirm", key=f"neg_{it['id']}"):
                        db.update_item(it["id"], status="negative",
                                       action_type="bought_original",
                                       savings=None, second_hand_price=None)
                        st.success("Action recorded (negative).")
                        _rerun()

                elif act == "Saved the money":
                    if st.button("Confirm", key=f"save_{it['id']}"):
                        db.update_item(it["id"], status="positive",
                                       action_type="saved_money",
                                       savings=it["price"], second_hand_price=None)
                        st.success("Action recorded (positive).")
                        _rerun()

                elif act == "Bought second-hand":
                    sp = st.number_input("Second-hand price (€)", min_value=0.0, step=0.5, key=f"sp_{it['id']}")
                    if st.button("Confirm", key=f"2h_{it['id']}"):
                        savings = max(it["price"] - float(sp), 0.0)
                        db.update_item(it["id"], status="positive",
                                       action_type="bought_second_hand",
                                       savings=savings, second_hand_price=float(sp))
                        st.success("Action recorded (positive, second-hand).")
                        _rerun()

# =========================
# Page: Metrics
# =========================
def metrics_page():
    st.header("📈 Metrics")
    username = st.session_state.get("username","anon")
    pos = db.list_user_items(username, status="positive", order_by="-created_at")
    neg = db.list_user_items(username, status="negative", order_by="-created_at")

    total_savings = sum([(x.get("savings") or 0) for x in pos])
    total_spent   = sum([(x.get("price") or 0) for x in neg])
    co2_pos = sum([(x.get("co2_estimate") or 0) for x in pos])
    co2_neg = sum([(x.get("co2_estimate") or 0) for x in neg])

    tabs = st.tabs(["Economic", "Environmental"])

    # --- Economic ---
    with tabs[0]:
        c1, c2 = st.columns(2)
        c1.metric("💚 Total savings", f"€{total_savings:.2f}")
        c2.metric("🟥 Spend on originals", f"€{total_spent:.2f}")

        st.subheader("Positive actions")
        df_pos = pd.DataFrame([{
            "id": x["id"], "title": x["title"], "savings": x.get("savings",0.0),
            "type": x.get("action_type",""), "date": x.get("created_at","")
        } for x in pos])
        st.dataframe(df_pos, use_container_width=True)

        st.subheader("Original purchases")
        df_neg = pd.DataFrame([{
            "id": x["id"], "title": x["title"], "price": x.get("price",0.0),
            "date": x.get("created_at","")
        } for x in neg])
        st.dataframe(df_neg, use_container_width=True)

        if llm_available():
            txt = call_llm([
                {"role":"system","content":"Respond in ≤60 words, empathetic and practical."},
                {"role":"user","content": f"Savings: €{total_savings:.2f} in {len(pos)} actions; Original spend: €{total_spent:.2f} in {len(neg)} purchases."}
            ])
            if txt:
                st.info(f"🧠 AI: {txt}")
        else:
            st.info(f"🧮 Summary: Savings €{total_savings:.2f} ({len(pos)} actions) · "
                    f"Original spend €{total_spent:.2f} ({len(neg)} purchases)")

    # --- Environmental ---
    with tabs[1]:
        c1, c2 = st.columns(2)
        c1.metric("🌿 CO₂ avoided", f"{co2_pos:.2f} kg")
        c2.metric("🔥 CO₂ generated", f"{co2_neg:.2f} kg")

        st.subheader("Positive (CO₂) detail")
        df_pos2 = pd.DataFrame([{
            "id": x["id"], "title": x["title"], "CO₂": x.get("co2_estimate",0.0),
            "level": x.get("co2_level","")
        } for x in pos])
        st.dataframe(df_pos2, use_container_width=True)

        st.subheader("Negative (CO₂) detail")
        df_neg2 = pd.DataFrame([{
            "id": x["id"], "title": x["title"], "CO₂": x.get("co2_estimate",0.0),
            "level": x.get("co2_level","")
        } for x in neg])
        st.dataframe(df_neg2, use_container_width=True)

        if llm_available():
            txt = call_llm([
                {"role":"system","content":"Respond in ≤60 words, inspiring and educational."},
                {"role":"user","content": f"CO₂ avoided {co2_pos:.2f} kg ({len(pos)} actions), CO₂ generated {co2_neg:.2f} kg ({len(neg)} purchases)."}
            ])
            if txt:
                st.info(f"🧠 AI: {txt}")
        else:
            st.info(f"🧮 Summary: CO₂ avoided {co2_pos:.2f} kg · CO₂ generated {co2_neg:.2f} kg")

# =========================
# Admin page (list users & items)
# =========================
def admin_db_view():
    st.header("🛠️ Admin · Database")
    st.caption("SQLite file path:")
    st.code(db.db_path(), language="bash")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Users")
        st.caption(f"Total users: {db.count_users()}")
        df_users = db.list_users_df()
        st.dataframe(df_users, use_container_width=True)
        st.download_button("⬇️ Export users",
                           df_users.to_csv(index=False).encode("utf-8"),
                           "users.csv", "text/csv", key="dl_users")
    with c2:
        st.subheader("Items")
        st.caption(f"Total items: {db.count_items()}")
        df_items = db.list_items_df()
        st.dataframe(df_items, use_container_width=True)
        st.download_button("⬇️ Export items",
                           df_items.to_csv(index=False).encode("utf-8"),
                           "items.csv", "text/csv", key="dl_items")

# =========================
# Main router
# =========================
def main():
    # Init DB + seed users
    db.init_db()
    db.seed_initial_users({
        "mario": {"password": "1234", "role": "Admin"},
        "lucas": {"password": "abcd", "role": "Manager"},
        "irene": {"password": "pass", "role": "Viewer"},
    })

    # NEW: Check for OAuth callback
    if handle_google_oauth_callback():
        return

    st.sidebar.title("SpendSense")
    if not is_authenticated():
        tabs = st.tabs(["Login", "Sign up"])
        with tabs[0]:
            show_login()
        with tabs[1]:
            show_signup()
        return

    # Authenticated zone
    username = st.session_state.get("username", "—")
    role = st.session_state.get("role", "Viewer")
    profile_picture = st.session_state.get("profile_picture")
    auth_provider = st.session_state.get("auth_provider", "local")

    # Show profile picture if available
    if profile_picture:
        col1, col2 = st.sidebar.columns([1, 3])
        with col1:
            st.image(profile_picture, width=50)
        with col2:
            st.write(f"**{username}**")
            st.caption(f"{role}")
            if auth_provider == "google":
                st.caption("🔵 Google")
    else:
        st.sidebar.write(f"👤 {username} ({role})")

    # Logout WITHOUT on_click (no rerun inside callback)
    if st.sidebar.button("Logout", key="logout_sidebar_btn"):
        logout()   # clears session
        _rerun()   # rerun outside callback

    pages = ["Upload item", "Cart", "Metrics", "Chatbot"]
    if role == "Admin":
        pages.append("Admin")
    page = st.sidebar.radio("Navigation", pages, index=0, key="nav_radio")

    if page == "Upload item":
        upload_page()
    elif page == "Cart":
        smart_cart_page()
    elif page == "Metrics":
        metrics_page()
    elif page == "Chatbot":
        chatbot_page()
    elif page == "Admin" and role == "Admin":
        admin_db_view()

if __name__ == "__main__":
    main()
