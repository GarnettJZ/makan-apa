import streamlit as st
import pandas as pd
import requests
from io import StringIO
from datetime import datetime, timedelta

# Page Config
st.set_page_config(page_title="APU Gap Finder", page_icon="🍱", layout="wide")

# Constants
HEADERS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
    'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
    'sec-fetch-site': 'none',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
}
BASE_URL = 'https://api.apiit.edu.my/timetable-print/index.php'
DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri"]

# --- Helper Functions ---

def get_start_of_week():
    """Auto-calculates the Monday of the current week."""
    today = datetime.now()
    start = today - timedelta(days=today.weekday())
    return start.strftime("%Y-%m-%d")

@st.cache_data(ttl=600)
def fetch_timetable_data(intake_code, group_code, week_date):
    """Fetches raw timetable HTML and returns a DataFrame."""
    params = {
        'Week': week_date,
        'Intake': intake_code,
        'Intake_Group': group_code,
        'print_request': 'print_tt'
    }
    
    try:
        response = requests.get(BASE_URL, headers=HEADERS, params=params)
        
        if "manupulate" in response.text:
            return "BLOCKED"
        
        # FIX: Clean malformed HTML from APU API (e.g. colspan="6 text-center")
        clean_html = response.text.replace('colspan="6 text-center"', 'colspan="6"')
            
        dfs = pd.read_html(StringIO(clean_html))
        
        for df in dfs:
            # Basic validation to check if it's the right table
            if "DATE" in df.to_string().upper(): 
                # Clean up MultiIndex headers if necessary
                if "DATE" not in str(df.columns).upper():
                     df.columns = df.iloc[0]
                     df = df[1:]
                return df
        return None
    except Exception:
        return None

def parse_time_str(time_str):
    """Parses '08:30 - 10:30' into (8.5, 10.5)."""
    try:
        start_str, end_str = time_str.split("-")
        sh, sm = map(int, start_str.strip().split(":"))
        eh, em = map(int, end_str.strip().split(":"))
        return sh + sm/60, eh + em/60
    except:
        return None, None

def extract_schedule(df):
    """
    Parses dataframe into a list of event dictionaries.
    Event structure:
    {
        'day': 'Mon', 
        'start': 8.5, 
        'end': 10.5, 
        'duration': 2.0,
        'subject': 'CT044-3-1-DSTR', 
        'type': 'L',  # Derived logic
        'location': 'B-05-04',
        'raw_date': 'Mon, 02-Feb-2026'
    }
    """
    schedule = []
    if isinstance(df, str) or df is None: return []
    
    # Normalize columns
    df.columns = [str(c).upper().strip() for c in df.columns]
    date_col = next((c for c in df.columns if "DATE" in c), None)
    time_col = next((c for c in df.columns if "TIME" in c), None)
    subj_col = next((c for c in df.columns if "SUBJECT" in c), None)
    loc_col = next((c for c in df.columns if "CLASSROOM" in c or "LOCATION" in c), None)
    
    if not date_col or not time_col: return []

    for _, row in df.iterrows():
        d_str = str(row[date_col])
        t_str = str(row[time_col])
        
        # Determine Day of Week
        day_match = None
        for day in DAYS_OF_WEEK:
            if day in d_str:
                day_match = day
                break
        
        if not day_match: continue
        
        # Parse Time
        start, end = parse_time_str(t_str)
        if start is None or end is None: continue
        
        # Extract Subject Info
        subject = str(row[subj_col]) if subj_col else "Unknown"
        location = str(row[loc_col]) if loc_col else ""
        
        # Simple heuristic for type (Lecture/Tutorial/Lab)
        # This is guesswork based on typical APU codes, can be refined
        class_type = "Class"
        if "(L)" in subject or "-L-" in subject: class_type = "Lecture"
        elif "(T)" in subject or "-T-" in subject: class_type = "Tutorial"
        elif "(LAB)" in subject or "-LAB-" in subject: class_type = "Lab"
        
        schedule.append({
            'day': day_match,
            'start': start,
            'end': end,
            'duration': end - start,
            'subject': subject,
            'type': class_type,
            'location': location,
            'is_gap': False
        })
        
    return schedule

def calculate_gaps(schedule):
    """Calculates gaps between classes for each day."""
    gaps = []
    # Process each day independently
    for day in DAYS_OF_WEEK:
        day_events = sorted([e for e in schedule if e['day'] == day], key=lambda x: x['start'])
        
        current_time = 8.0 # Start of day
        
        for event in day_events:
            if event['start'] > current_time:
                duration = event['start'] - current_time
                if duration >= 0.25: # Minimum 15 mins
                    gaps.append({
                        'day': day,
                        'start': current_time,
                        'end': event['start'],
                        'duration': duration,
                        'subject': "Gap",
                        'type': "Gap",
                        'is_gap': True,
                        'is_mutual': False
                    })
            current_time = max(current_time, event['end'])
            
        # Optional: Add end-of-day gap until 20.0 ?
        # For now, let's keep it strictly between classes or leading up to them if we want to fill the grid.
        # But specifically for "Makan Time", we usually care about gaps *between* classes.
        # To fill the grid visually, we might need filler blocks? 
        # Let's just track functional gaps.
        
    return gaps

def find_mutual_gaps(my_gaps, friend_gaps):
    """Finds overlapping gaps between two schedules."""
    mutual = []
    for m in my_gaps:
        for f in friend_gaps:
            if m['day'] == f['day']:
                # Calculate intersection
                start = max(m['start'], f['start'])
                end = min(m['end'], f['end'])
                
                if end - start >= 0.5: # Minimum 30 mins mutual
                   mutual.append({
                        'day': m['day'],
                        'start': start,
                        'end': end,
                        'duration': end - start,
                        'subject': "Mutual Gap",
                        'type': "Mutual",
                        'is_gap': True,
                        'is_mutual': True
                   }) 
    return mutual

# --- CSS / UI Components ---

def inject_custom_css():
    st.markdown("""
    <style>
    /* Main Grid Container */
    .timetable-grid {
        display: grid;
        grid-template-columns: 60px repeat(5, 1fr); /* Time col + 5 days */
        grid-template-rows: 40px repeat(48, 1fr); /* Header + 12 hours * 4 slots */
        column-gap: 8px; /* Gap between columns */
        row-gap: 0px; /* No gap between rows to form continuous lines */
        background-color: #fff;
        border: 1px solid #ddd;
        border-radius: 8px;
        padding-bottom: 20px;
        max-height: 85vh;
        overflow-y: auto;
        position: relative;
    }
    
    /* Headers */
    .grid-header {
        font-weight: bold;
        text-align: center;
        padding: 10px;
        color: #444;
        background: #f8f9fa;
        border-bottom: 2px solid #ddd;
        position: sticky;
        top: 0;
        z-index: 20;
    }
    .time-col-header { grid-column: 1; grid-row: 1; }
    
    /* Time Axis & Grid Lines */
    .time-label {
        grid-column: 1;
        font-size: 0.75rem;
        color: #666;
        text-align: right;
        padding-right: 8px;
        transform: translateY(-50%);
        align-self: start;
        font-variant-numeric: tabular-nums;
    }
    .time-label-minor {
        font-size: 0.65rem;
        color: #aaa;
    }
    
    /* Horizontal Grid Lines */
    .grid-line {
        grid-column: 2 / 8; /* Span across days */
        border-top: 1px solid #f0f0f0;
        z-index: 1;
        pointer-events: none;
    }
    .grid-line-major {
        border-top: 1px solid #e0e0e0;
    }

    /* Event Blocks */
    .event-card {
        padding: 6px;
        border-radius: 6px;
        font-size: 0.75rem;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        overflow: hidden;
        z-index: 10;
        margin: 1px 2px; /* Slight spacing matches design */
    }
    .event-card:hover {
        transform: scale(1.02);
        z-index: 15;
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    
    /* Types */
    .event-Lecture { background-color: #ffe4e1; border-left: 4px solid #ff6b6b; color: #820000; }
    .event-Tutorial { background-color: #e3f2fd; border-left: 4px solid #2196f3; color: #0d47a1; }
    .event-Lab { background-color: #e8f5e9; border-left: 4px solid #4caf50; color: #1b5e20; }
    .event-Gap { background-color: transparent; border: 1px dashed #ced4da; color: #868e96; justify-content: center; align-items: center; opacity: 0.7; }
    .event-Mutual { background-color: #fff3bf; border: 2px solid #fab005; color: #e67700; font-weight: bold; z-index: 12; }
    
    .event-title { font-weight: 700; margin-bottom: 2px; text-overflow: ellipsis; white-space: nowrap; overflow: hidden; }
    .event-meta { font-size: 0.7rem; opacity: 0.9; text-overflow: ellipsis; white-space: nowrap; overflow: hidden; }
    .break-duration { font-size: 0.8rem; font-weight: 600; text-align: center; }

    /* Custom Scrollbar */
    ::-webkit-scrollbar { width: 8px; }
    ::-webkit-scrollbar-track { background: #f1f1f1; }
    ::-webkit-scrollbar-thumb { background: #ccc; border-radius: 4px; }
    </style>
    """, unsafe_allow_html=True)

def render_grid_html(events):
    """Generates the HTML structure for the grid."""
    
    html = ['<div class="timetable-grid">']
    
    # Headers
    days = ["", "Mon", "Tue", "Wed", "Thu", "Fri"]
    for i, day in enumerate(days):
        html.append(f'<div class="grid-header" style="grid-column: {i+1}; grid-row: 1;">{day}</div>')
        
    start_hour = 8
    end_hour = 20
    slots_per_hour = 4
    total_slots = (end_hour - start_hour) * slots_per_hour
    
    # Render Grid Lines & Labels
    for i in range(total_slots + 1):
        row = i + 2
        
        # Grid line for every 15 mins
        line_class = "grid-line"
        if i % 4 == 0: line_class += " grid-line-major"
        html.append(f'<div class="{line_class}" style="grid-row: {row};"></div>')

        # Time Labels
        if i % 2 == 0: # Every 30 mins
            time_val = start_hour + (i / 4)
            hour = int(time_val)
            minute = int((time_val % 1) * 60)
            time_str = f"{hour:02}:{minute:02}"
            
            label_class = "time-label"
            if i % 4 != 0: label_class += " time-label-minor" # 30 min marks are minor
            
            html.append(f'<div class="{label_class}" style="grid-row: {row};">{time_str}</div>')
            
    # Events
    for e in events:
        day_idx = DAYS_OF_WEEK.index(e['day']) + 2 # Col 1 is time, Col 2 is Mon...
        
        # Calculate Row positions (Start at 8:00 = row 2)
        # Formula: (Time - 8) * 4 + 2
        start_row = int((e['start'] - 8) * 4) + 2
        span = int(e['duration'] * 4)
        end_row = start_row + span
        
        if e['is_gap']:
            content = f'<div class="break-duration">{int(e["duration"])}h {int((e["duration"]%1)*60)}m Gap</div>'
            if e.get("type") == "Mutual":
                 content = f'<div class="break-duration">⚡ MUTUAL: {int(e["duration"])}h {int((e["duration"]%1)*60)}m</div>'
        else:
            content = f"""
<div class="event-title">{e['subject']}</div>
<div class="event-meta">{e['location']}</div>
<div class="event-meta">{e.get('type', 'Class')}</div>"""
            
        html.append(f"""<div class="event-card event-{e.get('type', 'Class')}" style="grid-column: {day_idx}; grid-row: {start_row} / {end_row};">{content}</div>""")
        
    html.append('</div>')
    return "\n".join(html)

# --- Main App Logic ---

st.title("🍱 Makan Time Finder")

with st.container():
    col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
    with col1:
        my_intake = st.text_input("My Intake", "APD3F2601CS(CYB)")
        my_group = st.text_input("Group", "G3")
    with col2:
        friend_intake = st.text_input("Friend's Intake", "APD3F2601IT(CE)")
        friend_group = st.text_input("Group", "G1")
    with col3:
        week_start = st.text_input("Week Of", get_start_of_week())
    with col4:
        st.write("") # Spacer
        st.write("") 
        find_btn = st.button("Find Mutual", type="primary", use_container_width=True)

inject_custom_css()

if find_btn:
    with st.spinner("Crunching timetables..."):
        # Fetch
        my_df = fetch_timetable_data(my_intake, my_group, week_start)
        friend_df = fetch_timetable_data(friend_intake, friend_group, week_start)
        
        if my_df is None or friend_df is None:
            st.error("Error: Could not retrieve timetables. Check Intake Codes and Groups.")
        elif isinstance(my_df, str) and my_df == "BLOCKED":
            st.error("APU WAF Blocked the request. Please wait.")
        else:
            # Parse
            my_schedule = extract_schedule(my_df)
            friend_schedule = extract_schedule(friend_df)
            
            # Gaps
            my_gaps = calculate_gaps(my_schedule)
            friend_gaps = calculate_gaps(friend_schedule)
            mutual_gaps = find_mutual_gaps(my_gaps, friend_gaps)
            
            st.success(f"Found {len(mutual_gaps)} mutual breaks! 🍱")
            
            # Prepare Combined Event Lists for Rendering
            # We want each person's grid to show THEIR classes + MUTUAL gaps
            # Standard single-person gaps are hidden/optional? 
            # Prompt says "only show the mutual break". 
            # So let's include: MyClasses + MutualGaps (Highlighted). 
            # Standard gaps can be just empty space (not rendered as blocks).
            
            my_events_final = my_schedule + mutual_gaps
            friend_events_final = friend_schedule + mutual_gaps
            
            # Layout Side-by-Side
            grid_col1, grid_col2 = st.columns(2)
            
            with grid_col1:
                st.subheader(f"👤 {my_intake}")
                st.markdown(render_grid_html(my_events_final), unsafe_allow_html=True)
                
            with grid_col2:
                st.subheader(f"👥 {friend_intake}")
                st.markdown(render_grid_html(friend_events_final), unsafe_allow_html=True)