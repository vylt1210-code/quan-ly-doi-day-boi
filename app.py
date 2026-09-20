import streamlit as st
import pandas as pd
from datetime import date, time, datetime
import time as time_module
import random
import re
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


@st.cache_data(ttl=300, show_spinner=False)
def read_sheets(sheet_names_tuple, refresh_nonce=0):
    """Read only the requested sheets in ONE Sheets API batch request."""
    sheet_names = list(sheet_names_tuple)
    if not sheet_names:
        return {}

    ss = get_spreadsheet()
    spreadsheet_id = st.secrets["spreadsheet_id"]
    ranges = [f"'{name}'!A:AD" for name in sheet_names]

    last_error = None
    for attempt in range(4):
        try:
            result = ss.client.values_batch_get(
                spreadsheet_id,
                ranges,
                params={"majorDimension": "ROWS"},
            )
            break
        except gspread.exceptions.APIError as exc:
            last_error = exc
            if "429" not in str(exc) and "Quota exceeded" not in str(exc):
                raise
            if attempt == 3:
                raise
            time_module.sleep((2 ** attempt) * 3 + random.uniform(0, 1.0))
    else:
        raise last_error

    value_ranges = result.get("valueRanges", [])
    data = {}

    for index, sheet_name in enumerate(sheet_names):
        value_range = value_ranges[index] if index < len(value_ranges) else {}
        values = value_range.get("values", [])
        headers = HEADERS[sheet_name]

        if not values:
            data[sheet_name] = pd.DataFrame(columns=headers)
            continue

        raw_headers = [str(x).strip() for x in values[0]]
        rows = values[1:]
        records = []
        for row in rows:
            padded = list(row) + [""] * max(0, len(raw_headers) - len(row))
            records.append(dict(zip(raw_headers, padded)))

        df = pd.DataFrame(records)
        for col in headers:
            if col not in df.columns:
                df[col] = ""
        data[sheet_name] = df[headers].copy()

    return data


def read_sheet(sheet_name):
    return read_sheets((sheet_name,), st.session_state.get("_refresh_nonce", 0))[sheet_name].copy()


def clear_cache():
    # Kept for compatibility; refresh_nonce is the primary cache-busting mechanism.
    read_sheets.clear()


def refresh_now():
    """Invalidate the active page data and force a fresh Google Sheets read."""
    st.session_state["_refresh_nonce"] = st.session_state.get("_refresh_nonce", 0) + 1


def _queue_write(sheet_name, row):
    pending = st.session_state.setdefault("_pending_writes", [])
    pending.append((sheet_name, {k: str(v) for k, v in row.items()}))


def _queue_delete(sheet_name, row_id):
    pending = st.session_state.setdefault("_pending_deletes", [])
    pending.append((sheet_name, str(row_id)))


def apply_pending_mutations(loaded):
    """Keep the UI immediately consistent while Google Sheets propagates a write."""
    writes = st.session_state.get("_pending_writes", [])
    remaining_writes = []
    for sheet_name, row in writes:
        if sheet_name not in loaded:
            remaining_writes.append((sheet_name, row))
            continue
        df = loaded[sheet_name]
        row_id = str(row.get("id", ""))
        if "id" not in df.columns or not (df["id"].astype(str) == row_id).any():
            loaded[sheet_name] = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        # If the row is already present from Google Sheets, the remote copy wins.
    st.session_state["_pending_writes"] = remaining_writes

    deletes = st.session_state.get("_pending_deletes", [])
    remaining_deletes = []
    for sheet_name, row_id in deletes:
        if sheet_name not in loaded:
            remaining_deletes.append((sheet_name, row_id))
            continue
        df = loaded[sheet_name]
        if "id" in df.columns:
            still_there = df["id"].astype(str) == str(row_id)
            if still_there.any():
                loaded[sheet_name] = df.loc[~still_there].copy()
                remaining_deletes.append((sheet_name, row_id))
            # If it has disappeared remotely, the delete has propagated.
    st.session_state["_pending_deletes"] = remaining_deletes
    return loaded


def next_id_from_df(df):
    if df.empty or "id" not in df.columns:
        return 1
    ids = pd.to_numeric(df["id"], errors="coerce").dropna()
    return int(ids.max()) + 1 if not ids.empty else 1


def append_row(sheet_name, row):
    ws = get_ws(sheet_name)
    values = [row.get(col, "") for col in HEADERS[sheet_name]]

    for attempt in range(4):
        try:
            ws.append_row(values, value_input_option="USER_ENTERED")
            _queue_write(sheet_name, row)
            refresh_now()
            return
        except gspread.exceptions.APIError as exc:
            if "429" not in str(exc) and "Quota exceeded" not in str(exc):
                raise
            if attempt == 3:
                raise
            time_module.sleep((2 ** attempt) * 3 + random.uniform(0, 1.0))


def delete_row(sheet_name, row_id):
    """Delete a row by ID using one cached read, then invalidate cache."""
    df = read_sheet(sheet_name)
    if df.empty or "id" not in df.columns:
        return False

    matches = df.index[df["id"].astype(str).str.strip() == str(row_id).strip()].tolist()
    if not matches:
        return False

    spreadsheet_row = int(matches[0]) + 2
    ws = get_ws(sheet_name)

    for attempt in range(4):
        try:
            ws.delete_rows(spreadsheet_row)
            _queue_delete(sheet_name, row_id)
            refresh_now()
            return True
        except gspread.exceptions.APIError as exc:
            if "429" not in str(exc) and "Quota exceeded" not in str(exc):
                raise
            if attempt == 3:
                raise
            time_module.sleep((2 ** attempt) * 3 + random.uniform(0, 1.0))
    return False

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
if st.sidebar.button("🔄 Làm mới dữ liệu", width='stretch'):
    refresh_now()
    st.rerun()


# =========================================================
# LOAD DATA
# =========================================================

PAGE_SHEETS = {
    "🏠 Dashboard": ("schools", "classes", "schedules", "teachers"),
    "🏫 Quản lý trường": ("schools",),
    "📚 Quản lý lớp": ("schools", "classes"),
    "📅 Lịch dạy": ("schools", "classes", "teachers", "schedules"),
    "👨‍🏫 Giáo viên": ("teachers",),
    "📋 Điểm danh GV": ("teachers", "attendance"),
    "📊 KPI & tiền công": ("teachers", "classes", "schedules", "kpi"),
    "⚠️ Sự cố": ("incidents",),
    "💾 Dữ liệu": tuple(SHEETS),
}

try:
    loaded = read_sheets(tuple(PAGE_SHEETS[page]), st.session_state.get("_refresh_nonce", 0))
    loaded = apply_pending_mutations(loaded)
    schools = loaded.get("schools", pd.DataFrame(columns=HEADERS["schools"]))
    teachers = loaded.get("teachers", pd.DataFrame(columns=HEADERS["teachers"]))
    classes = loaded.get("classes", pd.DataFrame(columns=HEADERS["classes"]))
    schedules = loaded.get("schedules", pd.DataFrame(columns=HEADERS["schedules"]))
    attendance = loaded.get("attendance", pd.DataFrame(columns=HEADERS["attendance"]))
    kpi = loaded.get("kpi", pd.DataFrame(columns=HEADERS["kpi"]))
    incidents = loaded.get("incidents", pd.DataFrame(columns=HEADERS["incidents"]))
except Exception as e:
    if "429" in str(e) or "Quota exceeded" in str(e):
        st.warning(
            "Google Sheets đang tạm giới hạn số lần đọc (429). "
            "App đã giảm request và tự thử lại. Hãy chờ khoảng 1–2 phút rồi tải lại."
        )
        st.stop()
    st.error("Không thể đọc dữ liệu Google Sheets.")
    st.code(str(e))
    st.stop()

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

    # Cây quản lý: gốc -> các trường -> các lớp.
    st.markdown("### 🌳 Sơ đồ cây hệ thống trường")
    if schools.empty:
        st.info("Chưa có trường nào. Hãy thêm trường ở mục Quản lý trường.")
    else:
        def dot_escape(value):
            return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

        dot = [
            'digraph G {',
            'rankdir=TB;',
            'graph [bgcolor="transparent", pad="0.2", nodesep="0.35", ranksep="0.55"];',
            'node [shape=box, style="rounded,filled", fontname="Arial", fontsize=11, margin="0.12,0.08"];',
            'edge [color="#94a3b8", penwidth=1.4];',
            'root [label="🏊 ĐỘI DẠY BƠI", fillcolor="#dbeafe", color="#2563eb", penwidth=2];',
        ]
        class_map_all = classes.set_index("id")["class_name"].to_dict() if not classes.empty else {}
        for _, school in schools.iterrows():
            sid = str(school["id"])
            snode = "school_" + re.sub(r"[^A-Za-z0-9_]", "_", sid)
            label = dot_escape(school["name"])
            dot.append(f'{snode} [label="🏫 {label}", fillcolor="#ecfeff", color="#0891b2"];')
            dot.append(f'root -> {snode};')
            school_classes = classes[classes["school_id"].astype(str) == sid] if not classes.empty else pd.DataFrame()
            for _, cl in school_classes.iterrows():
                cid = str(cl["id"])
                cnode = "class_" + re.sub(r"[^A-Za-z0-9_]", "_", cid)
                clabel = dot_escape(f'{cl["class_name"]} ({cl["students"]} HS)')
                dot.append(f'{cnode} [label="📚 {clabel}", fillcolor="#f8fafc", color="#94a3b8"];')
                dot.append(f'{snode} -> {cnode};')
        dot.append("}")
        st.graphviz_chart("\n".join(dot), width='stretch')

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
            width='stretch',
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
            submit = st.form_submit_button("Lưu trường", width='stretch')

            if submit:
                if not name.strip():
                    st.error("Vui lòng nhập tên trường.")
                elif not schools.empty and name.strip() in schools["name"].astype(str).tolist():
                    st.error("Tên trường đã tồn tại.")
                else:
                    append_row(
                        "schools",
                        {
                            "id": next_id_from_df(schools),
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
        width='stretch',
        hide_index=True,
    )

    st.markdown("### 🗑️ Xóa trường")
    if not schools.empty:
        school_choices = {f'{r["id"]} — {r["name"]}': r["id"] for _, r in schools.iterrows()}
        del_school_label = st.selectbox("Chọn trường cần xóa", list(school_choices), key="del_school")
        if st.button("🗑️ Xóa trường đã chọn", type="secondary", width='stretch'):
            del_id = school_choices[del_school_label]
            has_classes = not classes.empty and (classes["school_id"].astype(str) == str(del_id)).any() if "classes" in locals() else False
            # This page only loads schools; fetch classes only if deletion is requested.
            if has_classes:
                st.error("Không thể xóa: trường này đang có lớp. Hãy xóa lớp trước.")
            else:
                # Delete now; dependent schedules are checked by a small fresh read.
                dep = read_sheets(("classes", "schedules"), st.session_state.get("_refresh_nonce", 0))
                if not dep["classes"].empty and (dep["classes"]["school_id"].astype(str) == str(del_id)).any():
                    st.error("Không thể xóa: trường này đang có lớp.")
                elif not dep["schedules"].empty and (dep["schedules"]["school_id"].astype(str) == str(del_id)).any():
                    st.error("Không thể xóa: trường này đang có lịch dạy.")
                else:
                    delete_row("schools", del_id)
                    st.success("Đã xóa trường.")
                    st.rerun()

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
            submit = st.form_submit_button("➕ Thêm lớp", width='stretch')

            if submit:
                append_row(
                    "classes",
                    {
                        "id": next_id_from_df(classes),
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
            width='stretch',
            hide_index=True,
        )

        st.markdown("### 🗑️ Xóa lớp")
        if not classes.empty:
            class_choices = {f'{r["id"]} — {r["class_name"]}': r["id"] for _, r in classes.iterrows()}
            del_class_label = st.selectbox("Chọn lớp cần xóa", list(class_choices), key="del_class")
            if st.button("🗑️ Xóa lớp đã chọn", type="secondary", width='stretch'):
                del_id = class_choices[del_class_label]
                dep = read_sheets(("schedules",), st.session_state.get("_refresh_nonce", 0))
                if not dep["schedules"].empty and (dep["schedules"]["class_id"].astype(str) == str(del_id)).any():
                    st.error("Không thể xóa: lớp này đang có lịch dạy.")
                else:
                    delete_row("classes", del_id)
                    st.success("Đã xóa lớp.")
                    st.rerun()

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
                    "➕ Thêm lịch", width='stretch'
                )

                if submit:
                    append_row(
                        "schedules",
                        {
                            "id": next_id_from_df(schedules),
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
                width='stretch',
                hide_index=True,
            )

            st.markdown("### 🗑️ Xóa lịch dạy")
            if not schedules.empty:
                schedule_choices = {f'{r["id"]} — {r["day"]} — {r["start_time"]}': r["id"] for _, r in schedules.iterrows()}
                del_schedule_label = st.selectbox("Chọn lịch cần xóa", list(schedule_choices), key="del_schedule")
                if st.button("🗑️ Xóa lịch đã chọn", type="secondary", width='stretch'):
                    delete_row("schedules", schedule_choices[del_schedule_label])
                    st.success("Đã xóa lịch dạy.")
                    st.rerun()

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
            "➕ Thêm giáo viên", width='stretch'
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
                        "id": next_id_from_df(teachers),
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
        width='stretch',
        hide_index=True,
    )

    st.markdown("### 🗑️ Xóa giáo viên")
    if not teachers.empty:
        teacher_choices = {f'{r["code"]} — {r["name"]}': r["id"] for _, r in teachers.iterrows()}
        del_teacher_label = st.selectbox("Chọn giáo viên cần xóa", list(teacher_choices), key="del_teacher")
        if st.button("🗑️ Xóa giáo viên đã chọn", type="secondary", width='stretch'):
            del_id = teacher_choices[del_teacher_label]
            selected_code = teachers.loc[teachers["id"].astype(str) == str(del_id), "code"].iloc[0]
            dep = read_sheets(("schedules", "attendance", "kpi"), st.session_state.get("_refresh_nonce", 0))
            used_schedule = (not dep["schedules"].empty) and dep["schedules"][["teacher1","teacher2","teacher3"]].astype(str).eq(str(selected_code)).any().any()
            used_att = (not dep["attendance"].empty) and (dep["attendance"]["teacher_code"].astype(str) == str(selected_code)).any()
            used_kpi = (not dep["kpi"].empty) and (dep["kpi"]["teacher_code"].astype(str) == str(selected_code)).any()
            if used_schedule or used_att or used_kpi:
                st.error("Không thể xóa: giáo viên này đã có lịch, điểm danh hoặc KPI.")
            else:
                delete_row("teachers", del_id)
                st.success("Đã xóa giáo viên.")
                st.rerun()

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
                "💾 Lưu điểm danh", width='stretch'
            )

            if submit:
                append_row(
                    "attendance",
                    {
                        "id": next_id_from_df(attendance),
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
            width='stretch',
            hide_index=True,
        )

        st.markdown("### 🗑️ Xóa điểm danh")
        if not attendance.empty:
            att_choices = {f'{r["id"]} — {r["day"]} — {r["teacher_code"]}': r["id"] for _, r in attendance.iterrows()}
            del_att_label = st.selectbox("Chọn bản ghi cần xóa", list(att_choices), key="del_att")
            if st.button("🗑️ Xóa điểm danh đã chọn", type="secondary", width='stretch'):
                delete_row("attendance", att_choices[del_att_label])
                st.success("Đã xóa điểm danh.")
                st.rerun()

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

    st.dataframe(result, width='stretch', hide_index=True)

    st.markdown("### 🗑️ Xóa bản ghi KPI")
    if not kpi.empty:
        kpi_choices = {f'{r["id"]} — {r["month"]} — {r["teacher_code"]}': r["id"] for _, r in kpi.iterrows()}
        del_kpi_label = st.selectbox("Chọn KPI cần xóa", list(kpi_choices), key="del_kpi")
        if st.button("🗑️ Xóa KPI đã chọn", type="secondary", width='stretch'):
            delete_row("kpi", kpi_choices[del_kpi_label])
            st.success("Đã xóa KPI.")
            st.rerun()

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
                "💾 Lưu KPI", width='stretch'
            )

            if submit:
                append_row(
                    "kpi",
                    {
                        "id": next_id_from_df(kpi),
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
            "💾 Lưu sự cố", width='stretch'
        )

        if submit:
            if not incident.strip():
                st.error("Vui lòng nhập mô tả sự cố.")
            else:
                append_row(
                    "incidents",
                    {
                        "id": next_id_from_df(incidents),
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
        width='stretch',
        hide_index=True,
    )

    st.markdown("### 🗑️ Xóa sự cố")
    if not incidents.empty:
        incident_choices = {f'{r["id"]} — {r["day"]} — {r["incident"][:50]}': r["id"] for _, r in incidents.iterrows()}
        del_inc_label = st.selectbox("Chọn sự cố cần xóa", list(incident_choices), key="del_incident")
        if st.button("🗑️ Xóa sự cố đã chọn", type="secondary", width='stretch'):
            delete_row("incidents", incident_choices[del_inc_label])
            st.success("Đã xóa sự cố.")
            st.rerun()

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
        df = loaded.get(name, pd.DataFrame(columns=HEADERS[name]))
        st.write(f"**{name}** — {len(df)} bản ghi")

    st.divider()

    st.write("### 📥 Xuất toàn bộ dữ liệu")

    excel = pd.ExcelWriter("/tmp/quan_ly_boi.xlsx", engine="openpyxl")

    for name in SHEETS:
        loaded.get(name, pd.DataFrame(columns=HEADERS[name])).to_excel(
            excel, sheet_name=name[:31], index=False
        )

    excel.close()

    with open("/tmp/quan_ly_boi.xlsx", "rb") as f:
        st.download_button(
            "📊 Tải Excel toàn bộ hệ thống",
            data=f,
            file_name=f"quan_ly_boi_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width='stretch',
        )
