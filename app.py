import streamlit as st
import pandas as pd
import requests
from io import StringIO
from datetime import datetime, timedelta

# --- PAGE SETUP ---
st.set_page_config(page_title="APU Gap Finder", page_icon="🍱")

# --- HEADERS (Your "VIP Pass") ---
# These are the exact headers you captured that work
HEADERS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
    'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
    'sec-fetch-site': 'none', # Critical for bypassing WAF
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
}

BASE_URL = 'https://api.apiit.edu.my/timetable-print/index.php'

# --- HELPER FUNCTIONS ---
def get_start_of_week():
    """Auto-calculates the Monday of the current week."""
    today = datetime.now()
    start = today - timedelta(days=today.weekday())
    return start.strftime("%Y-%m-%d")

@st.cache_data(ttl=600) # Cache for 10 mins so you don't spam the server
def fetch_and_parse(intake_code, group_code, week_date):
    params = {
        'Week': week_date,
        'Intake': intake_code,
        'Intake_Group': group_code,
        'print_request': 'print_tt'
    }
    
    try:
        response = requests.get(BASE_URL, headers=HEADERS, params=params)
        
        # Security Check
        if "manupulate" in response.text:
            return "BLOCKED"
            
        dfs = pd.read_html(StringIO(response.text))
        
        # Find the correct table
        for df in dfs:
            if "DATE" in df.to_string().upper():
                if "DATE" not in str(df.columns).upper():
                    df.columns = df.iloc[0]
                    df = df[1:]
                return df
        return None
    except Exception:
        return None

def extract_busy_slots(df):
    busy = []
    if isinstance(df, str) or df is None: return []
    
    df.columns = [str(c).upper().strip() for c in df.columns]
    date_col = next((c for c in df.columns if "DATE" in c), None)
    time_col = next((c for c in df.columns if "TIME" in c), None)
    
    if not date_col or not time_col: return []

    for _, row in df.iterrows():
        d, t = str(row[date_col]), str(row[time_col])
        if "-" in t and any(m in d for m in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]):
            day = d.split(",")[0].strip()
            try:
                s_str, e_str = t.split("-")
                sh, sm = map(int, s_str.strip().split(":"))
                eh, em = map(int, e_str.strip().split(":"))
                busy.append((day, sh + sm/60, eh + em/60))
            except: continue
    return busy

# --- THE PHONE GUI ---
st.title("🍱 Makan Time Finder")

# 1. Inputs (Saved defaults)
with st.expander("Configuration", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        my_intake = st.text_input("My Intake", "APD3F2601CS(CYB)")
        my_group = st.text_input("My Group", "G3")
    with col2:
        friend_intake = st.text_input("Friend Intake", "APD3F2601IT(CE)")
        friend_group = st.text_input("Friend Group", "G1")
    
    # Auto-select this Monday
    week_start = st.text_input("Week Starting (YYYY-MM-DD)", get_start_of_week())

# 2. The Button
if st.button("Find Mutual Gaps", type="primary", use_container_width=True):
    with st.spinner("Checking APSpace..."):
        # Fetch Data
        my_df = fetch_and_parse(my_intake, my_group, week_start)
        friend_df = fetch_and_parse(friend_intake, friend_group, week_start)
        
        if my_df == "BLOCKED" or friend_df == "BLOCKED":
            st.error("❌ Request Blocked by APU WAF. Try again later.")
        elif my_df is None or friend_df is None:
            st.error("⚠️ Could not find timetable data. Check Intake Codes.")
        else:
            # Calculate Gaps
            my_busy = extract_busy_slots(my_df)
            friend_busy = extract_busy_slots(friend_df)
            
            st.success(f"Found {len(my_busy)} classes for you and {len(friend_busy)} for them.")
            
            days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
            has_gaps = False
            
            for day in days:
                # 8am (8.0) to 8pm (20.0)
                timeline = {x/100: True for x in range(800, 2000, 25)}
                
                for d, s, e in my_busy + friend_busy:
                    if d == day:
                        for slot in timeline:
                            if s <= slot < e: timeline[slot] = False
                
                # Find continuous gaps
                gaps = []
                start = None
                sorted_slots = sorted(timeline.keys())
                
                for t in sorted_slots:
                    if timeline[t]:
                        if start is None: start = t
                    else:
                        if start:
                            if (t - start) >= 1.0: gaps.append((start, t))
                            start = None
                if start and (20.0 - start) >= 1.0: gaps.append((start, 20.0))
                
                if gaps:
                    has_gaps = True
                    st.subheader(f"📅 {day}")
                    for s, e in gaps:
                        s_fmt = f"{int(s):02}:{int((s%1)*60):02}"
                        e_fmt = f"{int(e):02}:{int((e%1)*60):02}"
                        st.info(f"**{s_fmt} - {e_fmt}**")

            if not has_gaps:
                st.warning("No 1-hour gaps found this week. 💀")