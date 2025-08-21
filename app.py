import json
import io
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
import streamlit as st

st.set_page_config(page_title="Woo Bulk Tracking Uploader", layout="wide")

st.title("Bulk FedEx Tracking → WooCommerce")
st.caption("Adds tracking via Woo Shipment Tracking, then marks orders Completed if tracking was saved.")

# --- Credentials: use secrets, no sidebar, never display values ---
SITE_SECRET = st.secrets.get("site")
CK_SECRET   = st.secrets.get("ck")
CS_SECRET   = st.secrets.get("cs")

site = SITE_SECRET
ck   = CK_SECRET
cs   = CS_SECRET

using_secrets = all([site, ck, cs])
status_col1, status_col2 = st.columns([1, 5])
with status_col1:
    st.markdown("### 🔒")
with status_col2:
    if using_secrets:
        st.success("Using stored secrets (not shown).")
    else:
        st.warning("Secrets not found. Enter credentials below. They won’t be stored and won’t be displayed.")

# Optional inline form if secrets are missing
if not using_secrets:
    with st.form("creds_form", clear_on_submit=False):
        site = st.text_input("Store URL", value="", placeholder="https://your-store.com")
        ck   = st.text_input("Consumer Key", value="", type="password", placeholder="ck_xxx")
        cs   = st.text_input("Consumer Secret", value="", type="password", placeholder="cs_xxx")
        ok = st.form_submit_button("Use these for this session")
    if not ok:
        st.stop()

# If still missing anything, stop safely
if not site or not ck or not cs:
    st.error("Credentials are required to proceed.")
    st.stop()

auth = HTTPBasicAuth(ck, cs)

# --- Upload payload: CSV / JSON / XLSX ---
st.subheader("Upload payload as CSV or JSON")
uploaded = st.file_uploader("Choose CSV/JSON/XLSX (max ~200MB)", type=["csv", "json", "xlsx"])
# (file_uploader ref & limits)  :contentReference[oaicite:1]{index=1}

sample_json = [
    {
        "order_id": 123,
        "tracking_provider": "Fedex",
        "tracking_number": "882687730973",
        "date_shipped": "2025-08-18",
        "status_shipped": 1
    }
]
with st.expander("See JSON schema"):
    st.code(json.dumps(sample_json, indent=2), language="json")

def load_dataframe(file) -> pd.DataFrame:
    name = file.name.lower()
    if name.endswith(".json"):
        data = json.load(io.TextIOWrapper(file, encoding="utf-8"))
        return pd.DataFrame(data)
    if name.endswith(".csv"):
        return pd.read_csv(file)
    if name.endswith(".xlsx"):
        try:
            return pd.read_excel(file)
        except Exception as e:
            st.error(f"Excel read error. Install openpyxl. Details: {e}")
            return pd.DataFrame()
    return pd.DataFrame()

def validate_df(df: pd.DataFrame):
    required = ["order_id", "tracking_provider", "tracking_number"]
    missing = [c for c in required if c not in df.columns]
    return len(missing) == 0, missing

def post_tracking(site_url: str, auth: HTTPBasicAuth, row: dict):
    order_id = int(row["order_id"])
    url = f"{site_url.rstrip('/')}/wp-json/wc-shipment-tracking/v3/orders/{order_id}/shipment-trackings"
    payload = {
        "tracking_provider": str(row["tracking_provider"]),
        "tracking_number":  str(row["tracking_number"]),
        "date_shipped":     str(row.get("date_shipped")) if row.get("date_shipped") else None,
        "status_shipped":   int(row.get("status_shipped", 1)),
        "replace_tracking": int(row.get("replace_tracking", 0))
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    r = requests.post(url, auth=auth, json=payload, timeout=30)
    try:
        data = r.json()
    except Exception:
        data = {"text": r.text}
    return r.status_code, data

def complete_order(site_url: str, auth: HTTPBasicAuth, order_id: int):
    url = f"{site_url.rstrip('/')}/wp-json/wc/v3/orders/{order_id}"
    payload = {"status": "completed"}
    r = requests.put(url, auth=auth, json=payload, timeout=30)
    try:
        data = r.json()
    except Exception:
        data = {"text": r.text}
    return r.status_code, data

if uploaded:
    df = load_dataframe(uploaded)
    ok, missing = validate_df(df)
    if not ok:
        st.error(f"Missing required columns: {', '.join(missing)}")
        st.stop()

    # Defaults for optional fields
    if "status_shipped" not in df.columns:
        df["status_shipped"] = 1
    if "replace_tracking" not in df.columns:
        df["replace_tracking"] = 0

    st.subheader("Preview")
    st.dataframe(df.head(20), use_container_width=True)

    run = st.button("Run Bulk Update")
    if run:
        results = []
        prog = st.progress(0)
        log = st.empty()
        total = len(df)

        for i, row in enumerate(df.to_dict(orient="records"), start=1):
            oid = int(row["order_id"])

            # 1) Add tracking
            t_status, t_data = post_tracking(site, auth, row)
            success_tracking = t_status in (200, 201)

            # 2) Only complete if tracking succeeded
            if success_tracking:
                c_status, c_data = complete_order(site, auth, oid)
                success_complete = c_status in (200, 201)
            else:
                c_status, c_data = None, {"skipped": True}
                success_complete = False

            results.append({
                "order_id": oid,
                "tracking_status": t_status,
                "tracking_ok": success_tracking,
                "complete_status": c_status,
                "complete_ok": success_complete,
                "tracking_response": t_data,
                "complete_response": c_data
            })

            log.write(f"Order {oid} tracking {t_status} → complete {c_status}")
            prog.progress(i / total)

        st.success("Done")
        out = pd.DataFrame(results)
        st.dataframe(out, use_container_width=True)
        csv = out.to_csv(index=False).encode("utf-8")
        st.download_button("Download results CSV", data=csv, file_name="tracking_results.csv", mime="text/csv")
