import streamlit as st
import pandas as pd
import requests
from io import StringIO, BytesIO
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.patches as patches

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

@st.cache_data
def get_available_weeks(data):
    """Extracts available weeks from the dataset."""
    if not data: return []
    dates = set()
    for item in data:
        if 'TIME_FROM_ISO' in item:
            dt = datetime.fromisoformat(item['TIME_FROM_ISO'])
            # Get Monday of the week
            monday = dt - timedelta(days=dt.weekday())
            dates.add(monday.date())
    
    sorted_mondays = sorted(list(dates))
    return [d.strftime("%Y-%m-%d") for d in sorted_mondays]

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
    # Filter by Intake, Group, and optionally Week
    filtered_items = []
    
    week_start_dt = None
    if week_date_str:
        week_start_dt = datetime.strptime(week_date_str, "%Y-%m-%d").date()
        # End of the week (Sunday)
        week_end_dt = week_start_dt + timedelta(days=6)
    
    for item in data:
        if item.get('INTAKE') != intake_code or item.get('GROUPING') != group_code:
            continue
            
        if week_start_dt:
            # Check if event falls within the week
            try:
                event_dt = datetime.fromisoformat(item.get('TIME_FROM_ISO')).date()
                if not (week_start_dt <= event_dt <= week_end_dt):
                    continue
            except:
                continue
                
        filtered_items.append(item)

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

def intersect_two_gap_lists(list1, list2):
    """Helper: Finds intersection between two lists of gaps."""
    intersection = []
    for g1 in list1:
        for g2 in list2:
            if g1['day'] == g2['day']:
                start = max(g1['start'], g2['start'])
                end = min(g1['end'], g2['end'])
                
                if end - start >= 0.5: # Minimum 30 mins mutual
                   intersection.append({
                        'day': g1['day'],
                        'start': start,
                        'end': end,
                        'duration': end - start,
                        'subject': "Mutual Gap",
                        'type': "Mutual",
                        'is_gap': True,
                        'is_mutual': True
                   })
    return intersection

def find_mutual_gaps(all_gap_lists):
    """Finds overlapping gaps across multiple schedules (N-way interaction)."""
    if not all_gap_lists: return []
    
    # Start with the first person's gaps
    current_mutual = all_gap_lists[0]
    
    # Intersect with every subsequent person
    for next_list in all_gap_lists[1:]:
        current_mutual = intersect_two_gap_lists(current_mutual, next_list)
        if not current_mutual:
            break # No intersection possible if empty
            
    return current_mutual

def generate_schedule_image(schedules_map, mutual_gaps):
    """Generates a matplotlib figure of the compared schedules."""
    
    n_cols = len(schedules_map)
    fig, axes = plt.subplots(1, n_cols, figsize=(5 * n_cols, 10), sharey=True)
    if n_cols == 1: axes = [axes] # Handle single plot case
    
    plt.subplots_adjust(wspace=0.1)
    
    # Colors
    colors = {
        'Lecture': '#ffe4e1', 'Tutorial': '#e3f2fd', 'Lab': '#e8f5e9', 
        'Gap': '#f8f9fa', 'Mutual': '#fff3bf'
    }
    edge_colors = {
        'Lecture': '#ff6b6b', 'Tutorial': '#2196f3', 'Lab': '#4caf50',
        'Gap': '#dee2e6', 'Mutual': '#fab005'
    }
    
    day_map_idx = {d: i for i, d in enumerate(DAYS_OF_WEEK)}
    
    for idx, (name, info) in enumerate(schedules_map.items()):
        ax = axes[idx]
        ax.set_title(f"{name}\n{info.get('intake','')}\n({info.get('group','')})", fontsize=10, pad=10)
        
        # Set grid
        ax.set_xlim(0, 5)
        ax.set_ylim(20, 8) # Inverted Y axis: 8am to 8pm
        ax.set_xticks([0.5, 1.5, 2.5, 3.5, 4.5])
        ax.set_xticklabels(DAYS_OF_WEEK)
        ax.set_yticks(range(8, 21))
        ax.grid(axis='y', linestyle='--', alpha=0.5)
        ax.tick_params(axis='x', length=0)
        
        # Combine events + mutual
        events_to_draw = info['data'] + mutual_gaps
        
        for e in events_to_draw:
            if e['day'] not in day_map_idx: continue
            
            x = day_map_idx[e['day']]
            y = e['start']
            height = e['duration']
            etype = e.get('type', 'Class')
            
            # Skip non-mutual gaps to keep it clean, or draw them lightly
            if etype == 'Gap' and not e.get('is_mutual'):
                continue
                
            rect = patches.Rectangle(
                (x, y), 1, height, 
                linewidth=1, 
                edgecolor=edge_colors.get(etype, '#999'), 
                facecolor=colors.get(etype, '#eee'),
                zorder=10 if etype == "Mutual" else 5
            )
            ax.add_patch(rect)
            
            # Text
            label = "MUTUAL BREAK" if etype == "Mutual" else f"{e['subject']}\n{e['location']}"
            if height >= 0.5: # Only label if enough space
                ax.text(
                    x + 0.5, y + 0.5 * height, 
                    label, 
                    ha='center', va='center', 
                    fontsize=7 if etype != "Mutual" else 8,
                    fontweight='bold' if etype == "Mutual" else 'normal',
                    color='#e67700' if etype == "Mutual" else '#333',
                    wrap=True, clip_on=True, zorder=15
                )

        # Draw vertical lines for days
        for x in range(1, 5):
            ax.axvline(x, color='#eee', linewidth=1)

    # Save to buffer
    buf = BytesIO()
    plt.savefig(buf, format="png", bbox_inches='tight', dpi=150)
    buf.seek(0)
    plt.close(fig)
    return buf

# --- CSS / UI Components ---

def inject_custom_css():
    st.markdown("""
    <style>
    /* Main Grid Container */
    .timetable-grid {
        display: grid;
        grid-template-columns: 60px repeat(5, 1fr); /* Time col + 5 days */
        grid-template-rows: 40px repeat(48, 20px); /* Header + 12 hours * 4 slots * 20px fixed height */
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

st.title("🍱 Bila Nak Makan?")

inject_custom_css()

# Fetch all data first
s3_data = fetch_s3_data()
all_intakes = get_intakes(s3_data)
available_weeks = get_available_weeks(s3_data)

# --- State Management & Persistence ---

# 1. Read Defaults from URL
query_params = st.query_params
default_my_intake = query_params.get("my_intake", "")
default_my_group = query_params.get("my_group", "")
default_friend_intake = query_params.get("friend_intake", "")
default_friend_group = query_params.get("friend_group", "")
default_friend2_intake = query_params.get("friend2_intake", "")
default_friend2_group = query_params.get("friend2_group", "")
default_week = query_params.get("week", "")

# 2. Toggle UI
# Auto-enable if friend 2 data is present in URL
show_friend_2 = st.checkbox("Add another friend?", value=bool(default_friend2_intake), key="toggle_f2")

# Layout logic
if show_friend_2:
    cols = st.columns([2, 2, 2, 1]) # Me, F1, F2, Week
else:
    cols = st.columns([2, 2, 1])    # Me, F1, Week

# --- Column 1: My Info ---
with cols[0]:
    st.write("My Intake")
    my_filter = st.text_input("🔍 Filter My Intake", placeholder="Type to search (e.g. CS)", key="filter_my")
    
    filtered_my_intakes = all_intakes
    if my_filter:
        filtered_my_intakes = [i for i in all_intakes if my_filter.upper() in i.upper()]
        
    my_ix = 0
    if default_my_intake in filtered_my_intakes:
        my_ix = filtered_my_intakes.index(default_my_intake)
        
    my_intake = st.selectbox("Select My Intake", filtered_my_intakes, index=my_ix, key="my_intake_code")
    my_groups = get_groups(s3_data, my_intake)
    
    my_g_ix = 0
    if default_my_group in my_groups:
        my_g_ix = my_groups.index(default_my_group)
    my_group = st.selectbox("My Group", my_groups, index=my_g_ix, key="my_group")

# --- Column 2: Friend 1 Info ---
with cols[1]:
    st.write("Friend 1")
    friend_filter = st.text_input("🔍 Filter Friend 1", placeholder="Search...", key="filter_friend")
    
    filtered_friend_intakes = all_intakes
    if friend_filter:
        filtered_friend_intakes = [i for i in all_intakes if friend_filter.upper() in i.upper()]
        
    f_ix = 0
    if default_friend_intake in filtered_friend_intakes:
         f_ix = filtered_friend_intakes.index(default_friend_intake)
    
    friend_intake = st.selectbox("Select Friend 1", filtered_friend_intakes, index=f_ix, key="friend_intake_code")
    friend_groups = get_groups(s3_data, friend_intake)
    
    f_g_ix = 0
    if default_friend_group in friend_groups:
        f_g_ix = friend_groups.index(default_friend_group)
    friend_group = st.selectbox("Friend 1 Group", friend_groups, index=f_g_ix, key="friend_group")

# --- Column 3 (Optional): Friend 2 Info ---
friend2_intake = None
friend2_group = None

if show_friend_2:
    with cols[2]:
        st.write("Friend 2")
        friend2_filter = st.text_input("🔍 Filter Friend 2", placeholder="Search...", key="filter_friend2")
        
        filtered_friend2_intakes = all_intakes
        if friend2_filter:
            filtered_friend2_intakes = [i for i in all_intakes if friend2_filter.upper() in i.upper()]
            
        f2_ix = 0
        if default_friend2_intake in filtered_friend2_intakes:
             f2_ix = filtered_friend2_intakes.index(default_friend2_intake)
        
        friend2_intake = st.selectbox("Select Friend 2", filtered_friend2_intakes, index=f2_ix, key="friend2_intake_code")
        friend2_groups = get_groups(s3_data, friend2_intake)
        
        f2_g_ix = 0
        if default_friend2_group in friend2_groups:
            f2_g_ix = friend2_groups.index(default_friend2_group)
        friend2_group = st.selectbox("Friend 2 Group", friend2_groups, index=f2_g_ix, key="friend2_group")

# --- Last Column: Time ---
with cols[-1]:
    st.write("Week")
    st.write("") 
    st.write("")
    
    default_w_ix = 0
    if default_week and default_week in available_weeks:
        default_w_ix = available_weeks.index(default_week)
    else:
        current_mon = get_start_of_week()
        if current_mon in available_weeks:
            default_w_ix = available_weeks.index(current_mon)
        
    selected_week = st.selectbox("Select Week", available_weeks, index=default_w_ix, key="week_select")
    st.write("")

# --- Sync to URL & Process ---
# Base validation
is_valid = my_intake and my_group and friend_intake and friend_group and selected_week
if show_friend_2:
    is_valid = is_valid and friend2_intake and friend2_group

if is_valid:
    # Update Params
    st.query_params["my_intake"] = my_intake
    st.query_params["my_group"] = my_group
    st.query_params["friend_intake"] = friend_intake
    st.query_params["friend_group"] = friend_group
    st.query_params["week"] = selected_week
    
    if show_friend_2:
        st.query_params["friend2_intake"] = friend2_intake
        st.query_params["friend2_group"] = friend2_group
    else:
        # Clear friend 2 params if toggled off
        if "friend2_intake" in st.query_params: del st.query_params["friend2_intake"]
        if "friend2_group" in st.query_params: del st.query_params["friend2_group"]
    
    # Process Schedules
    my_schedule = process_s3_schedule(s3_data, my_intake, my_group, selected_week)
    friend_schedule = process_s3_schedule(s3_data, friend_intake, friend_group, selected_week)
    
    schedules_map = {
        "Me": {"data": my_schedule, "intake": my_intake, "group": my_group},
        "Friend 1": {"data": friend_schedule, "intake": friend_intake, "group": friend_group}
    }
    
    if show_friend_2:
        f2_schedule = process_s3_schedule(s3_data, friend2_intake, friend2_group, selected_week)
        schedules_map["Friend 2"] = {"data": f2_schedule, "intake": friend2_intake, "group": friend2_group}

    # Check for empty schedules
    all_found = True
    for name, info in schedules_map.items():
        if not info["data"]:
            st.warning(f"No classes found for {name} ({info['intake']} - {info['group']}) in week starting {selected_week}.")
            all_found = False
            
    if all_found:
        # 1. Calc Gaps
        all_gap_lists = []
        for name, info in schedules_map.items():
            gaps = calculate_gaps(info["data"])
            all_gap_lists.append(gaps)
            info["gaps"] = gaps # Store for reference
        
        # 2. Find Mutual (N-way)
        mutual_gaps = find_mutual_gaps(all_gap_lists)
        
        if mutual_gaps:
             st.success(f"Found {len(mutual_gaps)} mutual breaks across {len(schedules_map)} schedules! 🍱")
        else:
             st.info("No mutual breaks found unfortunately.")
        
        # 3. Combine & Display
        cols_display = st.columns(len(schedules_map))
        
        idx = 0
        for name, info in schedules_map.items():
            display_events = info["data"] + mutual_gaps
            with cols_display[idx]:
                st.subheader(f"{'👤' if name=='Me' else '👥'} {info['intake']} ({info['group']})")
                st.markdown(render_grid_html(display_events), unsafe_allow_html=True)
            idx += 1
            
        # 4. Download Image Feature
        st.write("---")
        if st.checkbox("Show Export Options"):
            with st.spinner("Generating image..."):
                img_buf = generate_schedule_image(schedules_map, mutual_gaps)
                st.image(img_buf, caption="Preview", width=800)
                st.download_button(
                    label="📷 Download Comparison Image",
                    data=img_buf,
                    file_name="makan_schedule.png",
                    mime="image/png"
                )

else:
    st.info("Please select intakes and groups for all active friends.")

