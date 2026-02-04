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
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
}
DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri"]

# --- Helper Functions ---

def get_start_of_week():
    """Auto-calculates the Monday of the current week."""
    today = datetime.now()
    start = today - timedelta(days=today.weekday())
    return start.strftime("%Y-%m-%d")

@st.cache_data(ttl=21600)  # Cache for 6 hours
def fetch_s3_data():
    """Fetches key APU timetable data from S3."""
    try:
        url = "https://s3-ap-southeast-1.amazonaws.com/open-ws/weektimetable"
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"Failed to fetch data from S3: {e}")
        return []

@st.cache_data
def get_intakes(data):
    """Extracts unique sorted intake codes from S3 data."""
    if not data: return []
    intakes = sorted(list(set(item['INTAKE'] for item in data if 'INTAKE' in item)))
    return intakes

@st.cache_data
def get_groups(data, intake_code):
    """Extracts unique sorted groups for a specific intake."""
    if not data: return []
    groups = sorted(list(set(item['GROUPING'] for item in data if item.get('INTAKE') == intake_code)))
    return groups

def parse_iso_time(iso_str):
    """Parses ISO time string to decimal hour."""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.hour + dt.minute / 60
    except:
        return None

def process_s3_schedule(data, intake_code, group_code, week_date_str=None):
    """Processes S3 data for a specific intake and group."""
    schedule = []
    
    # Filter data
    # Note: week_date_str from date_input is used to filter events if data spans multiple weeks.
    # The current S3 dump seems to be a single week dump (based on previous analysis).
    
    filtered_items = [
        item for item in data 
        if item.get('INTAKE') == intake_code and item.get('GROUPING') == group_code
    ]

    for item in filtered_items:
        # Parse Day: S3 has "MON", "TUE" -> Convert to "Mon", "Tue"
        day_map = {
            'MON': 'Mon', 'TUE': 'Tue', 'WED': 'Wed', 'THU': 'Thu', 'FRI': 'Fri'
        }
        raw_day = item.get('DAY')
        day = day_map.get(raw_day)
        
        # Parse Time
        start = parse_iso_time(item.get('TIME_FROM_ISO'))
        end = parse_iso_time(item.get('TIME_TO_ISO'))
        
        if not day or start is None or end is None:
            continue

        # Extract Meta
        subject = item.get('MODULE_NAME', item.get('MODID', 'Unknown'))
        location = item.get('ROOM', item.get('LOCATION', 'Unknown'))
        modid = item.get('MODID', '')
        
        # Function to determine class type from MODID
        class_type = "Class"
        if "-L-" in modid or "(L)" in modid: class_type = "Lecture"
        elif "-T-" in modid or "(T)" in modid: class_type = "Tutorial"
        elif "-LAB-" in modid or "(LAB)" in modid: class_type = "Lab"

        schedule.append({
            'day': day,
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
st.write("Using APU's auto-generated timetable data.")

inject_custom_css()

# Fetch all data first
s3_data = fetch_s3_data()
all_intakes = get_intakes(s3_data)

with st.form("timetable_form"):
    c1, c2, c3 = st.columns([2, 2, 1])
    
    with c1:
        st.write("My Intake")
        # Default options and index logic can be added, for now just 0
        my_intake = st.selectbox("Select My Intake", all_intakes, key="my_intake_code", 
                                 help="Start typing to search your intake code")
        
        my_groups = get_groups(s3_data, my_intake)
        my_group = st.selectbox("My Group", my_groups, key="my_group")
        
    with c2:
        st.write("Friend's Intake")
        friend_intake = st.selectbox("Select Friend's Intake", all_intakes, key="friend_intake_code")
        
        friend_groups = get_groups(s3_data, friend_intake)
        friend_group = st.selectbox("Friend's Group", friend_groups, key="friend_group")

    with c3:
        week_input = st.date_input("Week Of", value=datetime.today())
        st.write("") # Spacer
        st.write("") 
        submitted = st.form_submit_button("Find Mutual", type="primary", use_container_width=True)

if submitted:
    if not (my_intake and my_group and friend_intake and friend_group):
        st.error("Please select valid intakes and groups for both persons.")
    else:
        # Process Schedules from S3 data (No fetching needed!)
        my_schedule = process_s3_schedule(s3_data, my_intake, my_group)
        friend_schedule = process_s3_schedule(s3_data, friend_intake, friend_group)
        
        if not my_schedule:
            st.warning(f"No classes found for {my_intake} ({my_group}).")
        if not friend_schedule:
            st.warning(f"No classes found for {friend_intake} ({friend_group}).")
            
        if my_schedule and friend_schedule:
            # 1. Add gaps
            my_schedule_gaps = calculate_gaps(my_schedule)
            friend_schedule_gaps = calculate_gaps(friend_schedule)
            
            # 2. Find mutual
            mutual_gaps = find_mutual_gaps(my_schedule_gaps, friend_schedule_gaps)
            
            if mutual_gaps:
                st.success(f"Found {len(mutual_gaps)} mutual breaks! 🍱")
            else:
                st.info("No mutual breaks found unfortunately.")
            
            # 3. Combine for display
            # We want to show: My Events + Mutual Gaps (highlighted)
            # Friend Events + Mutual Gaps (highlighted)
            
            my_display_events = my_schedule + mutual_gaps
            friend_display_events = friend_schedule + mutual_gaps
            
            # --- Render Side-by-Side Grids ---
            
            # Use columns to separate the two schedules
            col_me, col_friend = st.columns(2)
            
            with col_me:
                st.subheader(f"👤 {my_intake} ({my_group})")
                st.markdown(render_grid_html(my_display_events), unsafe_allow_html=True)
                
            with col_friend:
                st.subheader(f"👥 {friend_intake} ({friend_group})")
                st.markdown(render_grid_html(friend_display_events), unsafe_allow_html=True)