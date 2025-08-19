import json
import io
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
import streamlit as st

st.set_page_config(page_title="Woo Tracking Uploader", layout="wide")

st.title("Bulk FedEx Tracking → WooCommerce Orders")
st.caption("Uploads tracking via Woo Shipment Tracking, then marks orders Completed if tracking was saved")

# Secrets first, then sidebar overrides
default_site = st.secrets.get("site", "")
default_ck = st.secrets.get("ck", "")
default_cs = st.secrets.get("cs", "")

with st.sidebar:
    st.subheader("Connection")
    site = st.text_input("Store URL", value=default_site or "https://your-store.com", placeholder="https://your-store.com")
    ck = st.text_input("Consumer Key", value=default_ck, type="password")
    cs = st.text_input("Consumer Secret", value=default_cs, type="password")
    st.markdown("Use Secrets for production deployments")

st.markdown("Upload payload as CSV or JSON")
uploaded = st.file_uploader("Choose CSV or JSON", type=["csv", "json", "xlsx"])

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

def validate_df(df: pd.DataFrame) -> tuple[bool, list[str]]:
    required = ["order_id", "tracking_provider", "tracking_number"]
    missing = [c for c in required if c not in df.columns]
    return (len(missing) == 0, missing)

def post_tracking(site_url: str, auth: HTTPBasicAuth, row: dict) -> tuple[int, dict]:
    order_id = int(row["order_id"])
    url = f"{site_url.rstrip('/')}/wp-json/wc-shipment-tracking/v3/orders/{order_id}/shipment-trackings"
    payload = {
        "tracking_provider": str(row["tracking_provider"]),
        "tracking_number":  str(row["tracking_number"]),
        "date_shipped":     str(row.get("date_shipped")) if row.get("date_shipped") else None,
        "status_shipped":   int(row.get("status_shipped", 1)),
        "replace_tracking": int(row.get("replace_tracking", 0))
    }
    # Remove None keys
    payload = {k: v for k, v in payload.items() if v is not None}
    r = requests.post(url, auth=auth, json=payload, timeout=30)
    try:
        data = r.json()
    except Exception:
        data = {"text": r.text}
    return r.status_code, data

def complete_order(site_url: str, auth: HTTPBasicAuth, order_id: int) -> tuple[int, dict]:
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
    else:
        # Fill optional defaults
        if "status_shipped" not in df.columns:
            df["status_shipped"] = 1
        if "replace_tracking" not in df.columns:
            df["replace_tracking"] = 0

        st.subheader("Preview")
        st.dataframe(df.head(20), use_container_width=True)

        if st.button("Run Bulk Update"):
            if not site or not ck or not cs:
                st.error("Please provide Store URL, Consumer Key, and Consumer Secret")
            else:
                auth = HTTPBasicAuth(ck, cs)
                results = []
                prog = st.progress(0)
                log = st.empty()

                total = len(df)
                for i, row in enumerate(df.to_dict(orient="records"), start=1):
                    oid = int(row["order_id"])
                    t_status, t_data = post_tracking(site, auth, row)
                    success_tracking = t_status in (200, 201)

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

                    log.write(f"Order {oid} tracking {t_status} then complete {c_status}")
                    prog.progress(i / total)

                st.success("Done")
                out = pd.DataFrame(results)
                st.dataframe(out, use_container_width=True)

                csv = out.to_csv(index=False).encode("utf-8")
                st.download_button("Download results CSV", data=csv, file_name="tracking_results.csv", mime="text/csv")
