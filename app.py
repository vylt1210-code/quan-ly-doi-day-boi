import streamlit as st
import pandas as pd
from datetime import date, time, datetime
import gspread
from google.oauth2.service_account import Credentials

# =========================================================
# CONFIG
# =========================================================

st.set_page_config(
    page_title="Quản lý đội dạy bơi",
    page_icon="🏊",
    layout="wide",
    initial_sidebar_state="expanded",
)

SHEETS = [
    "schools",
    "teachers",
    "classes",
    "schedules",
    "attendance",
    "kpi",
    "incidents",
]

HEADERS = {
    "schools": ["id", "name", "address", "contact", "note"],
    "teachers": ["id", "code", "name", "phone", "role", "status", "note"],
    "classes": ["id", "school_id", "class_name", "students", "note"],
    "schedules": [
        "id", "day", "school_id", "class_id", "start_time", "end_time",
        "teacher1", "teacher2", "teacher3", "status", "note"
    ],
    "attendance": [
        "id", "day", "teacher_code", "present", "on_time", "full_shift", "note"
    ],
    "kpi": ["id", "month", "teacher_code", "deduction", "note"],
    "incidents": [
        "id", "day", "school", "class_name", "student", "incident",
        "teacher", "handling", "level", "status", "note"
    ],
}

# =========================================================
# GOOGLE SHEETS
# =========================================================

@st.cache_resource
def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes,
    )
    return gspread.authorize(credentials)


@st.cache_resource
def get_spreadsheet():
    client = get_client()
    spreadsheet_id = st.secrets["spreadsheet_id"]
    return client.open_by_key(spreadsheet_id)


@st.cache_resource
def get_worksheets():
    """Load worksheet metadata once and create only missing sheets."""
    ss = get_spreadsheet()
    worksheets = {ws.title: ws for ws in ss.worksheets()}

    for sheet_name in SHEETS:
        if sheet_name not in worksheets:
            worksheets[sheet_name] = ss.add_worksheet(
                title=sheet_name, rows=1000, cols=max(30, len(HEADERS[sheet_name]))
            )
            worksheets[sheet_name].append_row(HEADERS[sheet_name])
        else:
            ws = worksheets[sheet_name]
            # Do not call get_all_values() here. That used to consume one
            # Sheets API read request for every sheet on every app startup.

    return worksheets


def get_ws(sheet_name):
    worksheets = get_worksheets()
    return worksheets[sheet_name]


def init_sheets():
    # Calling this once only loads worksheet metadata / creates missing tabs.
    get_worksheets()


@st.cache_data(ttl=60, show_spinner=False)
def read_all_sheets():
    """Read all application sheets with ONE Sheets API batch request."""
    ss = get_spreadsheet()
    spreadsheet_id = st.secrets["spreadsheet_id"]
    ranges = [f"'{sheet_name}'!A:AD" for sheet_name in SHEETS]

    # gspread's low-level batch endpoint performs one HTTP request for all
    # ranges instead of one request per worksheet.
    result = ss.client.values_batch_get(
        spreadsheet_id,
        ranges,
        params={"majorDimension": "ROWS"},
    )

    value_ranges = result.get("valueRanges", [])
    data = {}

    for sheet_name, value_range in zip(SHEETS, value_ranges):
        values = value_range.get("values", [])
        headers = HEADERS[sheet_name]

        if not values:
            data[sheet_name] = pd.DataFrame(columns=headers)
            continue

        # Use the first row as headers, while always enforcing the schema
        # expected by the application.
        raw_headers = [str(x).strip() for x in values[0]]
        rows = values[1:]
        records = []
        for row in rows:
            padded = list(row) + [""] * max(0, len(raw_headers) - len(row))
            records.append(dict(zip(raw_headers, padded)))

        df = pd.DataFrame(records)
        if df.empty:
            df = pd.DataFrame(columns=headers)

        for col in headers:
            if col not in df.columns:
                df[col] = ""
        data[sheet_name] = df[headers].copy()

    # Defensive fallback if the API omits an empty range from the response.
    for sheet_name in SHEETS:
        data.setdefault(sheet_name, pd.DataFrame(columns=HEADERS[sheet_name]))

    return data


def read_sheet(sheet_name):
    return read_all_sheets()[sheet_name].copy()


def clear_cache():
    read_all_sheets.clear()


def next_id(sheet_name):
    df = read_sheet(sheet_name)
    if df.empty or "id" not in df.columns:
        return 1

    ids = pd.to_numeric(df["id"], errors="coerce").dropna()
    return int(ids.max()) + 1 if not ids.empty else 1


def append_row(sheet_name, row):
    ws = get_ws(sheet_name)
    ws.append_row(
        [row.get(col, "") for col in HEADERS[sheet_name]],
        value_input_option="USER_ENTERED",
    )
    clear_cache()


def delete_row(sheet_name, row_id):
    """Delete using cached dataframe position; no extra Sheets read."""
    df = read_sheet(sheet_name)
    if df.empty or "id" not in df.columns:
        return

    matches = df.index[df["id"].astype(str).str.strip() == str(row_id).strip()].tolist()
    if not matches:
        return

    # Data row 0 corresponds to spreadsheet row 2 because row 1 is headers.
    spreadsheet_row = int(matches[0]) + 2
    get_ws(sheet_name).delete_rows(spreadsheet_row)
    clear_cache()

# =========================================================
# HELPERS
# =========================================================

def money(value):
    return f"{float(value):,.0f} đ"


def clean_num(value, default=0):
    try:
        return float(value)
    except Exception:
        return default


def teacher_name(code, teachers):
    if not code:
        return ""
    match = teachers[teachers["code"].astype(str) == str(code)]
    return match.iloc[0]["name"] if not match.empty else code


# =========================================================
# STYLE
# =========================================================

st.markdown(
    """
    <style>
    .main-title {
        font-size: 34px;
        font-weight: 800;
        color: #05256e;
        margin-bottom: 10px;
    }
    .section-title {
        font-size: 22px;
        font-weight: 750;
        color: #05256e;
        margin-top: 12px;
    }
    [data-testid="stMetric"] {
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 12px;
        background: #f8fbff;
    }
    .data-card {
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 14px;
        background: white;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================================================
# CONNECT
# =========================================================

try:
    init_sheets()
except Exception as e:
    st.error("Không thể kết nối Google Sheets.")
    st.code(str(e))
    st.stop()

# =========================================================
# SIDEBAR
# =========================================================

st.sidebar.markdown("## 🏊 QUẢN LÝ ĐỘI DẠY BƠI")

page = st.sidebar.radio(
    "Chức năng",
    [
        "🏠 Dashboard",
        "🏫 Quản lý trường",
        "📚 Quản lý lớp",
        "📅 Lịch dạy",
        "👨‍🏫 Giáo viên",
        "📋 Điểm danh GV",
        "📊 KPI & tiền công",
        "⚠️ Sự cố",
        "💾 Dữ liệu",
    ],
)

st.sidebar.divider()
st.sidebar.caption("☁️ Google Sheets là kho dữ liệu chính.")
st.sidebar.caption("🔐 Không lưu database cục bộ trên Streamlit Cloud.")

# =========================================================
# LOAD DATA
# =========================================================

schools = read_sheet("schools")
teachers = read_sheet("teachers")
classes = read_sheet("classes")
schedules = read_sheet("schedules")
attendance = read_sheet("attendance")
kpi = read_sheet("kpi")
incidents = read_sheet("incidents")

# =========================================================
# DASHBOARD
# =========================================================

if page == "🏠 Dashboard":

    st.markdown(
        '<div class="main-title">🏊 DASHBOARD QUẢN LÝ ĐỘI GIÁO VIÊN DẠY BƠI</div>',
        unsafe_allow_html=True,
    )

    selected_day = st.date_input("Ngày quản lý", date.today())
    day_str = selected_day.strftime("%d/%m/%Y")

    day_schedule = schedules[schedules["day"].astype(str) == day_str].copy()

    total_students = pd.to_numeric(
        day_schedule["students"] if "students" in day_schedule else pd.Series(dtype=float),
        errors="coerce",
    ).sum()

    if not day_schedule.empty:
        day_schedule["students"] = day_schedule["class_id"].map(
            classes.set_index("id")["students"].to_dict()
        )
        total_students = pd.to_numeric(day_schedule["students"], errors="coerce").sum()

    active_teachers = teachers[
        teachers["status"].astype(str) == "Đang làm"
    ]

    a, b, c, d = st.columns(4)
    a.metric("🏫 Trường", day_schedule["school_id"].nunique())
    b.metric("📚 Lớp hôm nay", len(day_schedule))
    c.metric("👨‍🎓 Học sinh", int(total_students or 0))
    d.metric("👨‍🏫 Giáo viên", len(active_teachers))

    st.divider()

    if day_schedule.empty:
        st.info("Chưa có lịch dạy cho ngày này.")
    else:
        display = day_schedule.copy()
        school_map = schools.set_index("id")["name"].to_dict()
        class_map = classes.set_index("id")["class_name"].to_dict()

        display["Trường"] = display["school_id"].map(school_map)
        display["Lớp"] = display["class_id"].map(class_map)
        display["Số HS"] = display["class_id"].map(
            classes.set_index("id")["students"].to_dict()
        )
        display["GV1"] = display["teacher1"].map(
            lambda x: teacher_name(x, teachers)
        )
        display["GV2"] = display["teacher2"].map(
            lambda x: teacher_name(x, teachers)
        )
        display["GV3"] = display["teacher3"].map(
            lambda x: teacher_name(x, teachers)
        )

        st.dataframe(
            display[
                [
                    "Trường", "Lớp", "Số HS", "start_time", "end_time",
                    "GV1", "GV2", "GV3", "status", "note"
                ]
            ].rename(
                columns={
                    "start_time": "Bắt đầu",
                    "end_time": "Kết thúc",
                    "status": "Trạng thái",
                    "note": "Ghi chú",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

# =========================================================
# SCHOOLS
# =========================================================

elif page == "🏫 Quản lý trường":

    st.markdown("## 🏫 Quản lý trường")

    with st.expander("➕ Thêm trường", expanded=True):
        with st.form("school_form"):
            name = st.text_input("Tên trường")
            address = st.text_input("Địa chỉ")
            contact = st.text_input("Đầu mối liên hệ")
            note = st.text_area("Ghi chú")
            submit = st.form_submit_button("Lưu trường", use_container_width=True)

            if submit:
                if not name.strip():
                    st.error("Vui lòng nhập tên trường.")
                elif not schools.empty and name.strip() in schools["name"].astype(str).tolist():
                    st.error("Tên trường đã tồn tại.")
                else:
                    append_row(
                        "schools",
                        {
                            "id": next_id("schools"),
                            "name": name.strip(),
                            "address": address,
                            "contact": contact,
                            "note": note,
                        },
                    )
                    st.success("Đã lưu trường vào Google Sheets.")
                    st.rerun()

    st.dataframe(
        schools.rename(
            columns={
                "id": "ID",
                "name": "Tên trường",
                "address": "Địa chỉ",
                "contact": "Đầu mối",
                "note": "Ghi chú",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

# =========================================================
# CLASSES
# =========================================================

elif page == "📚 Quản lý lớp":

    st.markdown("## 📚 Quản lý lớp")

    school_map = dict(zip(schools["name"], schools["id"]))

    if not school_map:
        st.warning("Hãy thêm trường trước.")
    else:
        with st.form("class_form"):
            school_name = st.selectbox("Trường", list(school_map))
            class_name = st.text_input("Tên lớp")
            students_count = st.number_input(
                "Số học sinh", min_value=0, value=35, step=1
            )
            note = st.text_input("Ghi chú")
            submit = st.form_submit_button("➕ Thêm lớp", use_container_width=True)

            if submit:
                append_row(
                    "classes",
                    {
                        "id": next_id("classes"),
                        "school_id": school_map[school_name],
                        "class_name": class_name,
                        "students": students_count,
                        "note": note,
                    },
                )
                st.success("Đã thêm lớp.")
                st.rerun()

        display = classes.copy()
        display["Trường"] = display["school_id"].map(
            schools.set_index("id")["name"].to_dict()
        )

        st.dataframe(
            display[
                ["id", "Trường", "class_name", "students", "note"]
            ].rename(
                columns={
                    "id": "ID",
                    "class_name": "Lớp",
                    "students": "Số HS",
                    "note": "Ghi chú",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

# =========================================================
# SCHEDULE
# =========================================================

elif page == "📅 Lịch dạy":

    st.markdown("## 📅 Lịch dạy & phân công giáo viên")

    school_map = dict(zip(schools["name"], schools["id"]))
    active_teachers = teachers[
        teachers["status"].astype(str) == "Đang làm"
    ]
    teacher_options = [""] + active_teachers["code"].astype(str).tolist()

    if not school_map:
        st.warning("Hãy thêm trường và lớp trước.")
    else:
        school_name = st.selectbox("Chọn trường", list(school_map))
        school_id = school_map[school_name]

        school_classes = classes[
            classes["school_id"].astype(str) == str(school_id)
        ]

        if school_classes.empty:
            st.warning("Trường này chưa có lớp.")
        else:
            class_map = dict(
                zip(school_classes["class_name"], school_classes["id"])
            )

            with st.form("schedule_form"):
                c1, c2, c3 = st.columns(3)

                with c1:
                    day = st.date_input("Ngày", date.today())
                with c2:
                    class_name = st.selectbox("Lớp", list(class_map))
                with c3:
                    status = st.selectbox(
                        "Trạng thái",
                        ["Đã xếp", "Chưa xếp", "Đã hoàn thành", "Hủy"],
                    )

                c1, c2 = st.columns(2)
                with c1:
                    start = st.time_input("Giờ bắt đầu", time(7, 30))
                with c2:
                    end = st.time_input("Giờ kết thúc", time(8, 40))

                g1, g2, g3 = st.columns(3)
                with g1:
                    teacher1 = st.selectbox("GV1", teacher_options)
                with g2:
                    teacher2 = st.selectbox("GV2", teacher_options)
                with g3:
                    teacher3 = st.selectbox("GV3", teacher_options)

                note = st.text_input("Ghi chú")

                submit = st.form_submit_button(
                    "➕ Thêm lịch", use_container_width=True
                )

                if submit:
                    append_row(
                        "schedules",
                        {
                            "id": next_id("schedules"),
                            "day": day.strftime("%d/%m/%Y"),
                            "school_id": school_id,
                            "class_id": class_map[class_name],
                            "start_time": start.strftime("%H:%M"),
                            "end_time": end.strftime("%H:%M"),
                            "teacher1": teacher1,
                            "teacher2": teacher2,
                            "teacher3": teacher3,
                            "status": status,
                            "note": note,
                        },
                    )
                    st.success("Đã lưu lịch vào Google Sheets.")
                    st.rerun()

            display = schedules.copy()
            display["Trường"] = display["school_id"].map(
                schools.set_index("id")["name"].to_dict()
            )
            display["Lớp"] = display["class_id"].map(
                classes.set_index("id")["class_name"].to_dict()
            )

            st.dataframe(
                display[
                    [
                        "id", "day", "Trường", "Lớp", "start_time",
                        "end_time", "teacher1", "teacher2", "teacher3",
                        "status", "note"
                    ]
                ].rename(
                    columns={
                        "id": "ID",
                        "day": "Ngày",
                        "start_time": "Bắt đầu",
                        "end_time": "Kết thúc",
                        "teacher1": "GV1",
                        "teacher2": "GV2",
                        "teacher3": "GV3",
                        "status": "Trạng thái",
                        "note": "Ghi chú",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

# =========================================================
# TEACHERS
# =========================================================

elif page == "👨‍🏫 Giáo viên":

    st.markdown("## 👨‍🏫 Quản lý giáo viên")

    with st.form("teacher_form"):
        c1, c2 = st.columns(2)

        with c1:
            code = st.text_input("Mã GV")
            name = st.text_input("Họ và tên")
            phone = st.text_input("Số điện thoại")

        with c2:
            role = st.selectbox(
                "Vai trò", ["Trưởng nhóm", "Giáo viên", "Dự phòng"]
            )
            status = st.selectbox(
                "Trạng thái", ["Đang làm", "Tạm nghỉ", "Nghỉ"]
            )
            note = st.text_input("Ghi chú")

        submit = st.form_submit_button(
            "➕ Thêm giáo viên", use_container_width=True
        )

        if submit:
            if not code.strip() or not name.strip():
                st.error("Mã GV và họ tên là bắt buộc.")
            elif not teachers.empty and code.strip() in teachers["code"].astype(str).tolist():
                st.error("Mã giáo viên đã tồn tại.")
            else:
                append_row(
                    "teachers",
                    {
                        "id": next_id("teachers"),
                        "code": code.strip(),
                        "name": name.strip(),
                        "phone": phone,
                        "role": role,
                        "status": status,
                        "note": note,
                    },
                )
                st.success("Đã thêm giáo viên.")
                st.rerun()

    st.dataframe(
        teachers.rename(
            columns={
                "id": "ID",
                "code": "Mã GV",
                "name": "Họ tên",
                "phone": "SĐT",
                "role": "Vai trò",
                "status": "Trạng thái",
                "note": "Ghi chú",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

# =========================================================
# ATTENDANCE
# =========================================================

elif page == "📋 Điểm danh GV":

    st.markdown("## 📋 Điểm danh giáo viên")

    active_teachers = teachers[
        teachers["status"].astype(str) == "Đang làm"
    ]
    teacher_map = dict(zip(active_teachers["name"], active_teachers["code"]))

    if not teacher_map:
        st.warning("Chưa có giáo viên đang làm.")
    else:
        with st.form("attendance_form"):
            day = st.date_input("Ngày", date.today())
            teacher_name_selected = st.selectbox(
                "Giáo viên", list(teacher_map)
            )
            present = st.checkbox("Có mặt", True)
            on_time = st.checkbox("Đúng giờ", True)
            full_shift = st.checkbox("Đủ ca", True)
            note = st.text_input("Ghi chú")

            submit = st.form_submit_button(
                "💾 Lưu điểm danh", use_container_width=True
            )

            if submit:
                append_row(
                    "attendance",
                    {
                        "id": next_id("attendance"),
                        "day": day.strftime("%d/%m/%Y"),
                        "teacher_code": teacher_map[teacher_name_selected],
                        "present": int(present),
                        "on_time": int(on_time),
                        "full_shift": int(full_shift),
                        "note": note,
                    },
                )
                st.success("Đã lưu điểm danh.")
                st.rerun()

        display = attendance.copy()
        display["Giáo viên"] = display["teacher_code"].map(
            teachers.set_index("code")["name"].to_dict()
        )

        st.dataframe(
            display[
                [
                    "id", "day", "teacher_code", "Giáo viên",
                    "present", "on_time", "full_shift", "note"
                ]
            ].rename(
                columns={
                    "id": "ID",
                    "day": "Ngày",
                    "teacher_code": "Mã GV",
                    "present": "Có mặt",
                    "on_time": "Đúng giờ",
                    "full_shift": "Đủ ca",
                    "note": "Ghi chú",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

# =========================================================
# KPI
# =========================================================

elif page == "📊 KPI & tiền công":

    st.markdown("## 📊 KPI & tiền công")

    month = st.text_input(
        "Tháng", value=datetime.now().strftime("%m/%Y")
    )
    unit_price = st.number_input(
        "Đơn giá / học sinh / lượt",
        min_value=0,
        value=5000,
        step=500,
    )

    schedule_data = schedules.copy()

    if not schedule_data.empty:
        schedule_data["students"] = schedule_data["class_id"].map(
            classes.set_index("id")["students"].to_dict()
        )
        total_students_sessions = pd.to_numeric(
            schedule_data["students"], errors="coerce"
        ).sum()
    else:
        total_students_sessions = 0

    total_pool = total_students_sessions * unit_price

    st.metric("Tổng sản lượng", money(total_pool))

    base = total_pool / max(len(teachers), 1)
    rows = []

    for _, teacher in teachers.iterrows():
        existing = kpi[
            (kpi["month"].astype(str) == month)
            & (kpi["teacher_code"].astype(str) == str(teacher["code"]))
        ]

        deduction = (
            clean_num(existing.iloc[-1]["deduction"])
            if not existing.empty
            else 0
        )

        kpi_value = max(0, 100 - deduction)
        actual = base * kpi_value / 100
        held = base - actual

        if kpi_value >= 95:
            grade = "Tốt"
        elif kpi_value >= 90:
            grade = "Đạt"
        elif kpi_value >= 80:
            grade = "Cảnh báo"
        else:
            grade = "Không đạt"

        rows.append(
            [
                teacher["code"],
                teacher["name"],
                deduction,
                kpi_value,
                base,
                actual,
                held,
                grade,
            ]
        )

    result = pd.DataFrame(
        rows,
        columns=[
            "Mã GV", "Giáo viên", "Điểm trừ", "KPI %",
            "Mức 100%", "Thực nhận", "Phần giữ lại", "Xếp loại"
        ],
    )

    st.dataframe(result, use_container_width=True, hide_index=True)

    st.markdown("### Nhập điểm trừ KPI")

    if not teachers.empty:
        with st.form("kpi_form"):
            teacher_code = st.selectbox(
                "Giáo viên", teachers["code"].astype(str).tolist()
            )
            deduction = st.number_input(
                "Tổng điểm trừ",
                min_value=0.0,
                max_value=100.0,
                value=0.0,
                step=1.0,
            )
            note = st.text_input("Lý do / ghi chú")

            submit = st.form_submit_button(
                "💾 Lưu KPI", use_container_width=True
            )

            if submit:
                append_row(
                    "kpi",
                    {
                        "id": next_id("kpi"),
                        "month": month,
                        "teacher_code": teacher_code,
                        "deduction": deduction,
                        "note": note,
                    },
                )
                st.success("Đã lưu KPI vào Google Sheets.")
                st.rerun()

# =========================================================
# INCIDENTS
# =========================================================

elif page == "⚠️ Sự cố":

    st.markdown("## ⚠️ Quản lý sự cố")

    with st.form("incident_form"):
        c1, c2 = st.columns(2)

        with c1:
            day = st.date_input("Ngày", date.today())
            school = st.text_input("Trường")
            class_name = st.text_input("Lớp")
            student = st.text_input("Học sinh")

        with c2:
            incident = st.text_area("Mô tả sự cố")
            teacher = st.text_input("Giáo viên phụ trách")
            level = st.selectbox("Mức độ", ["Mức 1", "Mức 2", "Mức 3"])
            status = st.selectbox(
                "Trạng thái",
                ["Đã xử lý", "Đang xử lý", "Cần báo cáo"],
            )

        handling = st.text_area("Cách xử lý")
        note = st.text_input("Ghi chú")

        submit = st.form_submit_button(
            "💾 Lưu sự cố", use_container_width=True
        )

        if submit:
            if not incident.strip():
                st.error("Vui lòng nhập mô tả sự cố.")
            else:
                append_row(
                    "incidents",
                    {
                        "id": next_id("incidents"),
                        "day": day.strftime("%d/%m/%Y"),
                        "school": school,
                        "class_name": class_name,
                        "student": student,
                        "incident": incident,
                        "teacher": teacher,
                        "handling": handling,
                        "level": level,
                        "status": status,
                        "note": note,
                    },
                )
                st.success("Đã lưu sự cố.")
                st.rerun()

    st.dataframe(
        incidents.rename(
            columns={
                "id": "ID",
                "day": "Ngày",
                "school": "Trường",
                "class_name": "Lớp",
                "student": "Học sinh",
                "incident": "Sự cố",
                "teacher": "GV",
                "handling": "Xử lý",
                "level": "Mức độ",
                "status": "Trạng thái",
                "note": "Ghi chú",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

# =========================================================
# DATA MANAGEMENT
# =========================================================

elif page == "💾 Dữ liệu":

    st.markdown("## 💾 Trung tâm dữ liệu")

    st.info(
        "Google Sheets là dữ liệu chính. Bạn có thể mở Google Sheets "
        "để kiểm tra, lọc, sao lưu hoặc tải dữ liệu bất kỳ lúc nào."
    )

    ss = get_spreadsheet()

    st.write("**Tên file Google Sheets:**", ss.title)

    st.write("### Các bảng dữ liệu")
    for name in SHEETS:
        df = read_sheet(name)
        st.write(f"**{name}** — {len(df)} bản ghi")

    st.divider()

    st.write("### 📥 Xuất toàn bộ dữ liệu")

    excel = pd.ExcelWriter("/tmp/quan_ly_boi.xlsx", engine="openpyxl")

    for name in SHEETS:
        read_sheet(name).to_excel(excel, sheet_name=name[:31], index=False)

    excel.close()

    with open("/tmp/quan_ly_boi.xlsx", "rb") as f:
        st.download_button(
            "📊 Tải Excel toàn bộ hệ thống",
            data=f,
            file_name=f"quan_ly_boi_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
