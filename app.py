import os
import hashlib
import streamlit as st
from dotenv import load_dotenv

from services.scraper import scrape_html
from services.cleaner import clean_html
from services.planner import generate_extraction_plan
from services.extractor import extract_rows_from_plan
from services.postprocess import postprocess_rows

# Local filtering only
from services.filtering import filter_rows

# Load environment variables
load_dotenv()

# ✅ MUST be the first Streamlit command
st.set_page_config(
    page_title="Prompt2Scrape",
    layout="wide",
)

# Minimal dark theme (KEEP EXACTLY SAME AS YOUR VERSION)
st.markdown(
    """
    <style>
      .stApp { background: #000000; color: #ffffff; }
      input, textarea { color: #ffffff !important; }
      .block-container { padding-top: 2rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------
# Caching helpers
# -----------------------------
@st.cache_data(show_spinner=False, ttl=60 * 30)
def cached_scrape(url: str):
    return scrape_html(url=url, timeout_ms=30_000, retries=2)

@st.cache_data(show_spinner=False, ttl=60 * 30)
def cached_clean(html: str):
    return clean_html(html)

def _hash_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

@st.cache_data(show_spinner=False, ttl=60 * 30)
def cached_plan(prompt: str, cleaned_html: str, cleaned_text: str, model: str):
    return generate_extraction_plan(
        user_prompt=prompt,
        cleaned_html=cleaned_html,
        cleaned_text=cleaned_text,
        model=model,
    )

@st.cache_data(show_spinner=False, ttl=60 * 30)
def cached_extract(cleaned_html: str, plan: dict):
    plan_str = str(plan)
    _ = _hash_str(plan_str)
    return extract_rows_from_plan(cleaned_html, plan)

@st.cache_data(show_spinner=False, ttl=60 * 30)
def cached_postprocess(rows: list):
    return postprocess_rows(rows)

def render_error(msg: str, e: Exception, debug: bool):
    st.error(msg)
    if debug:
        st.exception(e)
    else:
        st.caption("Tip: Enable Debug mode in the sidebar to see full error details.")


# -----------------------------
# Centered layout
# -----------------------------
left, center, right = st.columns([1, 2, 1])

with st.sidebar:
    st.header("Settings")
    debug_mode = st.toggle("Debug mode", value=False)
    use_cache = st.toggle("Use cache (faster)", value=True)
    st.caption("Cache avoids repeated scraping/planning/extraction.")

with center:
    st.title("Prompt2Scrape")

    url = st.text_input(
        "Website URL",
        placeholder="https://example.com/products",
        label_visibility="collapsed",
    )

    prompt = st.text_area(
        "Prompt",
        placeholder="e.g., Extract only hoodies above 10000: name, subtitle/product type, price",
        height=90,
        label_visibility="collapsed",
    )

    run = st.button("Extract", use_container_width=True)

    if run:
        if not url.strip():
            st.error("Please enter a URL.")
            st.stop()

        if not prompt.strip():
            st.error("Please enter an extraction prompt.")
            st.stop()

        # OpenAI key check early (planner uses OpenAI)
        if not os.getenv("OPENAI_API_KEY"):
            st.error("OPENAI_API_KEY not found. Add it to your .env file.")
            st.stop()

        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        # store debug payloads
        debug_payload = {"plan": None}

        with st.status("Running pipeline…", expanded=True) as status:
            # Stage 2: Scrape
            status.update(label="Scraping…", state="running")
            try:
                scrape_res = cached_scrape(url.strip()) if use_cache else scrape_html(
                    url=url.strip(), timeout_ms=30_000, retries=2
                )
            except Exception as e:
                status.update(label="Scraping failed", state="error")
                render_error("Scrape failed.", e, debug_mode)
                st.stop()

            st.success("Scrape successful.")
            st.write(
                f"**Final URL:** {scrape_res.final_url}\n\n"
                f"**HTTP status:** {scrape_res.status}\n\n"
                f"**HTML length:** {len(scrape_res.html):,} chars\n\n"
                f"**Time:** {scrape_res.elapsed_ms} ms"
            )

            # Stage 3: Clean
            status.update(label="Cleaning HTML…", state="running")
            try:
                clean_res = cached_clean(scrape_res.html) if use_cache else clean_html(scrape_res.html)
            except Exception as e:
                status.update(label="Cleaning failed", state="error")
                render_error("Cleaner failed.", e, debug_mode)
                st.stop()

            st.success("HTML cleaned.")
            st.write(
                f"**Original HTML:** {clean_res.original_len:,} chars\n\n"
                f"**Cleaned HTML:** {clean_res.cleaned_len:,} chars\n\n"
                f"**Extracted Text:** {clean_res.text_len:,} chars"
            )

            # Stage 4: Plan
            status.update(label="Planning extraction (OpenAI)…", state="running")
            try:
                plan_res = cached_plan(
                    prompt=prompt.strip(),
                    cleaned_html=clean_res.cleaned_html,
                    cleaned_text=clean_res.cleaned_text,
                    model=model,
                ) if use_cache else generate_extraction_plan(
                    user_prompt=prompt.strip(),
                    cleaned_html=clean_res.cleaned_html,
                    cleaned_text=clean_res.cleaned_text,
                    model=model,
                )
            except Exception as e:
                status.update(label="Planning failed", state="error")
                render_error("Planner failed.", e, debug_mode)
                st.stop()

            debug_payload["plan"] = plan_res.plan
            st.success(f"Plan generated. (model={plan_res.model}, attempts={plan_res.attempts})")

            # Stage 5: Extract
            status.update(label="Extracting…", state="running")
            try:
                ext_res = cached_extract(clean_res.cleaned_html, plan_res.plan) if use_cache else extract_rows_from_plan(
                    clean_res.cleaned_html, plan_res.plan
                )
            except Exception as e:
                status.update(label="Extraction failed", state="error")
                render_error("Extractor failed.", e, debug_mode)
                st.stop()

            st.success(
                f"Extraction done. Matched items: {ext_res.item_count:,} | Rows: {len(ext_res.rows):,}"
            )

            if not ext_res.rows:
                status.update(label="No rows extracted", state="error")
                st.warning("No rows extracted. Try changing the prompt or URL.")
                st.stop()

            # Stage 7: Local Filtering (universal)
            status.update(label="Filtering…", state="running")
            filtered_rows, filter_meta = filter_rows(ext_res.rows, prompt.strip())

            if filter_meta.get("applied"):
                st.info(
                    f"Filters applied: {', '.join(filter_meta['applied'])} | "
                    f"Rows: {filter_meta['before']} → {filter_meta['after']}"
                )
            else:
                st.info(f"No filters detected in prompt. Rows: {len(ext_res.rows)}")

            if not filtered_rows:
                status.update(label="No rows after filtering", state="error")
                st.warning("All rows were filtered out. Try relaxing the prompt.")
                st.stop()

            # Stage 6: Postprocess
            status.update(label="Postprocessing…", state="running")
            try:
                post = cached_postprocess(filtered_rows) if use_cache else postprocess_rows(filtered_rows)
            except Exception as e:
                status.update(label="Postprocessing failed", state="error")
                render_error("Postprocess failed.", e, debug_mode)
                st.stop()

            if post.df.empty:
                status.update(label="Empty dataset after postprocess", state="error")
                st.warning("Postprocessing produced an empty dataset.")
                st.stop()

            status.update(label="Done ", state="complete")

        # Debug UI OUTSIDE status block
        if debug_mode and debug_payload["plan"] is not None:
            with st.expander("DEBUG: Extraction plan JSON"):
                st.json(debug_payload["plan"])

        # Output: Download + Preview
        st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)

        st.download_button(
            label="Download CSV",
            data=post.csv_bytes,
            file_name="prompt2scrape_output.csv",
            mime="text/csv",
            use_container_width=True,
        )

        st.caption("first 10 rows")
        st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
        st.dataframe(post.df.head(10), use_container_width=True, hide_index=True)


