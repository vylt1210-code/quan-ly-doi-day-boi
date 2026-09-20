import streamlit as st
import pandas as pd
import gspread
import re
import random
import time as time_module

from datetime import date, time, datetime
from io import BytesIO
from google.oauth2.service_account import Credentials


# =========================================================
# CẤU HÌNH
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
    "schools": [
        "id",
        "name",
        "address",
        "contact",
        "note",
    ],

    "teachers": [
        "id",
        "code",
        "name",
        "phone",
        "role",
        "status",
        "note",
    ],

    "classes": [
        "id",
        "school_id",
        "class_name",
        "students",
        "note",
    ],

    "schedules": [
        "id",
        "day",
        "school_id",
        "class_id",
        "start_time",
        "end_time",
        "teacher1",
        "teacher2",
        "teacher3",
        "status",
        "note",
    ],

    "attendance": [
        "id",
        "day",
        "teacher_code",
        "present",
        "on_time",
        "full_shift",
        "note",
    ],

    "kpi": [
        "id",
        "month",
        "teacher_code",
        "deduction",
        "note",
    ],

    "incidents": [
        "id",
        "day",
        "school",
        "class_name",
        "student",
        "incident",
        "teacher",
        "handling",
        "level",
        "status",
        "note",
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
    return get_client().open_by_key(
        st.secrets["spreadsheet_id"]
    )


def is_quota_error(error):
    text = str(error)
    return (
        "429" in text
        or "Quota exceeded" in text
        or "Read requests per minute" in text
    )


def retry_google(function, attempts=4):
    last_error = None
    for attempt in range(attempts):
        try:
            return function()
        except Exception as error:
            last_error = error
            if not is_quota_error(error):
                raise
            if attempt == attempts - 1:
                raise
            time_module.sleep(
                3 * (2 ** attempt) + random.uniform(0.2, 0.8)
            )
    raise last_error


# =========================================================
# ĐỌC TOÀN BỘ SHEETS
# =========================================================

@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def load_google_data(cache_version=0):
    spreadsheet = get_spreadsheet()
    spreadsheet_id = st.secrets["spreadsheet_id"]

    ranges = [
        f"'{sheet}'!A:AD"
        for sheet in SHEETS
    ]

    result = retry_google(
        lambda: spreadsheet.client.values_batch_get(
            spreadsheet_id,
            ranges,
            params={
                "majorDimension": "ROWS"
            },
        )
    )

    value_ranges = result.get(
        "valueRanges",
        [],
    )

    all_data = {}

    for index, sheet_name in enumerate(SHEETS):
        if index < len(value_ranges):
            values = value_ranges[index].get(
                "values",
                [],
            )
        else:
            values = []

        headers = HEADERS[sheet_name]

        if not values:
            all_data[sheet_name] = pd.DataFrame(
                columns=headers
            )
            continue

        raw_headers = [
            str(x).strip()
            for x in values[0]
        ]

        rows = values[1:]
        records = []

        for row in rows:
            padded = list(row)
            while len(padded) < len(raw_headers):
                padded.append("")

            records.append(
                dict(
                    zip(
                        raw_headers,
                        padded,
                    )
                )
            )

        df = pd.DataFrame(records)

        for column in headers:
            if column not in df.columns:
                df[column] = ""

        df = df[headers].copy()
        all_data[sheet_name] = df

    return all_data


# =========================================================
# SESSION DATA
# =========================================================

def initialize_data():
    if "_app_data" not in st.session_state:
        version = st.session_state.get(
            "_cache_version",
            0,
        )
        st.session_state["_app_data"] = (
            load_google_data(version)
        )


def get_data(sheet):
    return st.session_state["_app_data"][sheet]


def set_data(sheet, dataframe):
    st.session_state["_app_data"][sheet] = (
        dataframe
        .reset_index(drop=True)
        .copy()
    )


# =========================================================
# ID
# =========================================================

def next_id(dataframe):
    if dataframe.empty:
        return 1

    if "id" not in dataframe.columns:
        return 1

    numbers = pd.to_numeric(
        dataframe["id"],
        errors="coerce",
    ).dropna()

    if numbers.empty:
        return 1

    return int(numbers.max()) + 1


# =========================================================
# GHI DỮ LIỆU
# =========================================================

def append_record(sheet, record):
    worksheet = get_spreadsheet().worksheet(sheet)

    values = [
        record.get(column, "")
        for column in HEADERS[sheet]
    ]

    retry_google(
        lambda: worksheet.append_row(
            values,
            value_input_option="USER_ENTERED",
        )
    )

    current = get_data(sheet)

    new_row = pd.DataFrame(
        [record],
        columns=HEADERS[sheet],
    )

    set_data(
        sheet,
        pd.concat(
            [
                current,
                new_row,
            ],
            ignore_index=True,
        ),
    )


# =========================================================
# XÓA DỮ LIỆU
# =========================================================

def delete_record(sheet, record_id):
    dataframe = get_data(sheet).copy()

    if dataframe.empty:
        return False

    matches = dataframe.index[
        dataframe["id"].astype(str)
        == str(record_id)
    ].tolist()

    if not matches:
        return False

    dataframe_index = matches[0]
    google_row = dataframe_index + 2

    worksheet = get_spreadsheet().worksheet(sheet)

    retry_google(
        lambda: worksheet.delete_rows(
            google_row
        )
    )

    dataframe = dataframe.drop(
        index=dataframe_index
    )

    set_data(
        sheet,
        dataframe,
    )

    return True


# =========================================================
# LÀM MỚI
# =========================================================

def refresh_data():
    st.session_state["_cache_version"] = (
        st.session_state.get(
            "_cache_version",
            0,
        ) + 1
    )

    load_google_data.clear()

    st.session_state.pop(
        "_app_data",
        None,
    )


# =========================================================
# HÀM PHỤ
# =========================================================

def teacher_name(code, teachers):
    if not code:
        return ""

    result = teachers[
        teachers["code"].astype(str)
        == str(code)
    ]

    if result.empty:
        return str(code)

    return str(
        result.iloc[0]["name"]
    )


def money(value):
    try:
        return f"{float(value):,.0f} đ"
    except Exception:
        return "0 đ"


# =========================================================
# KẾT NỐI
# =========================================================

try:
    initialize_data()
except Exception as error:
    if is_quota_error(error):
        st.error(
            "⚠️ Google Sheets đang giới hạn số lần đọc."
        )
        st.info(
            "Chờ khoảng 1–2 phút rồi bấm "
            "'🔄 Làm mới dữ liệu'."
        )
    else:
        st.error(
            "❌ Không thể kết nối Google Sheets."
        )
        st.code(
            str(error)
        )
    st.stop()


# =========================================================
# LẤY DATA
# =========================================================

schools = get_data("schools")
teachers = get_data("teachers")
classes = get_data("classes")
schedules = get_data("schedules")
attendance = get_data("attendance")
kpi = get_data("kpi")
incidents = get_data("incidents")


# =========================================================
# GIAO DIỆN
# =========================================================

st.markdown(
    """
    <style>
    .main-title {
        font-size: 34px;
        font-weight: 800;
        color: #05256e;
    }
    .section-title {
        font-size: 24px;
        font-weight: 750;
        color: #05256e;
    }
    [data-testid="stMetric"] {
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 12px;
        background: #f8fbff;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# SIDEBAR
# =========================================================

st.sidebar.markdown(
    "## 🏊 QUẢN LÝ ĐỘI DẠY BƠI"
)

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

st.sidebar.caption(
    "☁️ Google Sheets là dữ liệu chính."
)

if st.sidebar.button(
    "🔄 Làm mới dữ liệu",
    width="stretch",
):
    refresh_data()
    st.rerun()


# =========================================================
# DASHBOARD
# =========================================================

if page == "🏠 Dashboard":

    st.markdown(
        '<div class="main-title">'
        '🏊 DASHBOARD QUẢN LÝ ĐỘI DẠY BƠI'
        '</div>',
        unsafe_allow_html=True,
    )

    selected_day = st.date_input(
        "Ngày quản lý",
        date.today(),
    )

    day_string = selected_day.strftime(
        "%d/%m/%Y"
    )

    day_schedule = schedules[
        schedules["day"].astype(str)
        == day_string
    ].copy()

    school_map = {}
    if not schools.empty:
        school_map = dict(
            zip(
                schools["id"].astype(str),
                schools["name"],
            )
        )

    class_map = {}
    if not classes.empty:
        class_map = dict(
            zip(
                classes["id"].astype(str),
                classes["class_name"],
            )
        )

    student_map = {}
    if not classes.empty:
        student_map = dict(
            zip(
                classes["id"].astype(str),
                pd.to_numeric(
                    classes["students"],
                    errors="coerce",
                ).fillna(0),
            )
        )

    if day_schedule.empty:
        total_schools = 0
        total_students = 0
    else:
        total_schools = (
            day_schedule["school_id"]
            .astype(str)
            .nunique()
        )
        total_students = sum(
            student_map.get(
                str(class_id),
                0,
            )
            for class_id
            in day_schedule["class_id"]
        )

    active_teachers = teachers[
        teachers["status"].astype(str)
        == "Đang làm"
    ]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🏫 Trường hôm nay", total_schools)
    c2.metric("📚 Lớp hôm nay", len(day_schedule))
    c3.metric("👨‍🎓 Học sinh", int(total_students))
    c4.metric("👨‍🏫 GV đang làm", len(active_teachers))

    st.divider()

    st.markdown("### 🌳 Sơ đồ cây đội dạy bơi")

    if schools.empty:
        st.info("Chưa có trường. Vào 'Quản lý trường' để thêm.")
    else:
        dot = [
            "digraph G {",
            "rankdir=TB;",
            'graph [bgcolor="transparent"];',
            'node [shape=box, style="rounded,filled", fontname="Arial"];',
            'edge [color="#94a3b8"];',
            'root [label="🏊 ĐỘI DẠY BƠI", fillcolor="#dbeafe", color="#2563eb", penwidth=2];',
        ]

        for _, school in schools.iterrows():
            sid = str(school["id"])
            snode = "school_" + re.sub(r"[^A-Za-z0-9_]", "_", sid)
            school_name = str(school["name"]).replace('"', '\\"')

            dot.append(f'{snode} [label="🏫 {school_name}", fillcolor="#ecfeff", color="#0891b2"];')
            dot.append(f"root -> {snode};")

            school_classes = classes[
                classes["school_id"].astype(str) == sid
            ]

            for _, cls in school_classes.iterrows():
                cid = str(cls["id"])
                cnode = "class_" + re.sub(r"[^A-Za-z0-9_]", "_", cid)
                class_name = str(cls["class_name"]).replace('"', '\\"')
                students_count = str(cls["students"])

                dot.append(f'{cnode} [label="📚 {class_name}\\n{students_count} học sinh", fillcolor="#f8fafc", color="#94a3b8"];')
                dot.append(f"{snode} -> {cnode};")

        dot.append("}")

        st.graphviz_chart("\n".join(dot), width="stretch")

    st.divider()

    st.markdown("### 📅 Lịch dạy trong ngày")

    if day_schedule.empty:
        st.info("Chưa có lịch dạy trong ngày này.")
    else:
        display = day_schedule.copy()
        display["Trường"] = display["school_id"].astype(str).map(school_map)
        display["Lớp"] = display["class_id"].astype(str).map(class_map)
        display["GV1"] = display["teacher1"].map(lambda x: teacher_name(x, teachers))
        display["GV2"] = display["teacher2"].map(lambda x: teacher_name(x, teachers))
        display["GV3"] = display["teacher3"].map(lambda x: teacher_name(x, teachers))
        display["Số HS"] = display["class_id"].astype(str).map(student_map)

        st.dataframe(
            display[
                [
                    "Trường",
                    "Lớp",
                    "Số HS",
                    "start_time",
                    "end_time",
                    "GV1",
                    "GV2",
                    "GV3",
                    "status",
                    "note",
                ]
            ].rename(
                columns={
                    "start_time": "Bắt đầu",
                    "end_time": "Kết thúc",
                    "status": "Trạng thái",
                    "note": "Ghi chú",
                }
            ),
            width="stretch",
            hide_index=True,
        )


# =========================================================
# QUẢN LÝ TRƯỜNG
# =========================================================

elif page == "🏫 Quản lý trường":

    st.markdown("## 🏫 Quản lý trường")

    with st.form("school_form", clear_on_submit=True):
        name = st.text_input("Tên trường")
        address = st.text_input("Địa chỉ")
        contact = st.text_input("Đầu mối liên hệ")
        note = st.text_area("Ghi chú")
        save = st.form_submit_button("💾 Lưu trường", width="stretch")

    if save:
        name = name.strip()
        if not name:
            st.error("Vui lòng nhập tên trường.")
        else:
            existing = schools[
                schools["name"].astype(str).str.casefold() == name.casefold()
            ]

            if not existing.empty:
                st.error("Trường này đã tồn tại.")
            else:
                try:
                    new_record = {
                        "id": next_id(schools),
                        "name": name,
                        "address": address.strip(),
                        "contact": contact.strip(),
                        "note": note.strip(),
                    }
                    append_record("schools", new_record)
                    st.success("✅ Đã lưu trường.")
                    st.rerun()
                except Exception as error:
                    st.error("Không thể lưu trường.")
                    st.code(str(error))

    st.markdown("### 📋 Danh sách trường")

    if schools.empty:
        st.info("Chưa có trường.")
    else:
        table = schools.rename(
            columns={
                "id": "ID",
                "name": "Tên trường",
                "address": "Địa chỉ",
                "contact": "Đầu mối",
                "note": "Ghi chú",
            }
        )
        st.dataframe(table, width="stretch", hide_index=True)

    st.markdown("### 🗑️ Xóa trường")

    if schools.empty:
        st.info("Chưa có trường để xóa.")
    else:
        options = {
            f'{row["id"]} — {row["name"]}': row["id"]
            for _, row in schools.iterrows()
        }

        selected = st.selectbox(
            "Chọn trường cần xóa",
            list(options.keys()),
            key="delete_school",
        )

        if st.button("🗑️ Xóa trường đã chọn", width="stretch"):
            school_id = options[selected]

            has_class = (
                not classes.empty
                and (classes["school_id"].astype(str) == str(school_id)).any()
            )

            has_schedule = (
                not schedules.empty
                and (schedules["school_id"].astype(str) == str(school_id)).any()
            )

            if has_class or has_schedule:
                st.error("Không thể xóa trường vì trường vẫn còn lớp hoặc lịch dạy.")
            else:
                try:
                    delete_record("schools", school_id)
                    st.success("✅ Đã xóa trường.")
                    st.rerun()
                except Exception as error:
                    st.error("Không thể xóa trường.")
                    st.code(str(error))


# =========================================================
# QUẢN LÝ LỚP
# =========================================================

elif page == "📚 Quản lý lớp":

    st.markdown("## 📚 Quản lý lớp")

    if schools.empty:
        st.warning("Hãy thêm trường trước.")
    else:
        school_options = {
            str(row["name"]): row["id"]
            for _, row in schools.iterrows()
        }

        with st.form("class_form", clear_on_submit=True):
            school_name = st.selectbox("Trường", list(school_options.keys()))
            class_name = st.text_input("Tên lớp")
            students = st.number_input("Số học sinh", min_value=0, value=30, step=1)
            note = st.text_input("Ghi chú")
            save = st.form_submit_button("➕ Thêm lớp", width="stretch")

        if save:
            class_name = class_name.strip()
            if not class_name:
                st.error("Vui lòng nhập tên lớp.")
            else:
                school_id = school_options[school_name]
                duplicate = classes[
                    (classes["school_id"].astype(str) == str(school_id))
                    & (classes["class_name"].astype(str).str.casefold() == class_name.casefold())
                ]

                if not duplicate.empty:
                    st.error("Lớp này đã tồn tại.")
                else:
                    append_record(
                        "classes",
                        {
                            "id": next_id(classes),
                            "school_id": school_id,
                            "class_name": class_name,
                            "students": int(students),
                            "note": note.strip(),
                        },
                    )
                    st.success("✅ Đã thêm lớp.")
                    st.rerun()

        display = classes.copy()
        school_names = dict(
            zip(
                schools["id"].astype(str),
                schools["name"],
            )
        )

        display["Trường"] = display["school_id"].astype(str).map(school_names)

        st.dataframe(
            display[
                [
                    "id",
                    "Trường",
                    "class_name",
                    "students",
                    "note",
                ]
            ].rename(
                columns={
                    "id": "ID",
                    "class_name": "Lớp",
                    "students": "Số HS",
                    "note": "Ghi chú",
                }
            ),
            width="stretch",
            hide_index=True,
        )

        st.markdown("### 🗑️ Xóa lớp")

        if not classes.empty:
            options = {
                f'{row["id"]} — {row["class_name"]}': row["id"]
                for _, row in classes.iterrows()
            }

            selected = st.selectbox(
                "Chọn lớp cần xóa",
                list(options.keys()),
                key="delete_class",
            )

            if st.button("🗑️ Xóa lớp đã chọn", width="stretch"):
                class_id = options[selected]
                used = (
                    not schedules.empty
                    and (schedules["class_id"].astype(str) == str(class_id)).any()
                )

                if used:
                    st.error("Không thể xóa lớp vì lớp đang có lịch dạy.")
                else:
                    delete_record("classes", class_id)
                    st.success("✅ Đã xóa lớp.")
                    st.rerun()


# =========================================================
# LỊCH DẠY
# =========================================================

elif page == "📅 Lịch dạy":

    st.markdown("## 📅 Lịch dạy")

    if schools.empty:
        st.warning("Hãy thêm trường trước.")
    elif teachers.empty:
        st.warning("Hãy thêm giáo viên trước.")
    else:
        school_options = {
            str(row["name"]): row["id"]
            for _, row in schools.iterrows()
        }

        school_name = st.selectbox("Trường", list(school_options.keys()))
        school_id = school_options[school_name]

        school_classes = classes[
            classes["school_id"].astype(str) == str(school_id)
        ]

        if school_classes.empty:
            st.warning("Trường này chưa có lớp. Hãy tạo lớp trước.")
        else:
            class_options = {
                str(row["class_name"]): row["id"]
                for _, row in school_classes.iterrows()
            }

            teacher_options = {"-- Không chọn --": ""}
            for _, row in teachers.iterrows():
                teacher_options[f'{row["code"]} - {row["name"]}'] = row["code"]

            with st.form("schedule_form", clear_on_submit=True):
                c1, c2 = st.columns(2)
                with c1:
                    class_select = st.selectbox("Lớp", list(class_options.keys()))
                    sched_day = st.date_input("Ngày dạy", date.today())
                    start_t = st.time_input("Giờ bắt đầu", time(8, 0))
                    end_t = st.time_input("Giờ kết thúc", time(9, 30))

                with c2:
                    t1 = st.selectbox("Giáo viên 1", list(teacher_options.keys()), index=1 if len(teacher_options) > 1 else 0)
                    t2 = st.selectbox("Giáo viên 2", list(teacher_options.keys()), index=0)
                    t3 = st.selectbox("Giáo viên 3", list(teacher_options.keys()), index=0)
                    status_sched = st.selectbox("Trạng thái", ["Sắp diễn ra", "Đang học", "Hoàn thành", "Hủy"])

                note_sched = st.text_input("Ghi chú lịch dạy")
                save_sched = st.form_submit_button("➕ Thêm lịch dạy", width="stretch")

            if save_sched:
                class_id = class_options[class_select]
                t1_code = teacher_options[t1]
                t2_code = teacher_options[t2]
                t3_code = teacher_options[t3]

                if not t1_code:
                    st.error("Vui lòng chọn ít nhất Giáo viên 1.")
                else:
                    new_sched = {
                        "id": next_id(schedules),
                        "day": sched_day.strftime("%d/%m/%Y"),
                        "school_id": school_id,
                        "class_id": class_id,
                        "start_time": start_t.strftime("%H:%M"),
                        "end_time": end_t.strftime("%H:%M"),
                        "teacher1": t1_code,
                        "teacher2": t2_code,
                        "teacher3": t3_code,
                        "status": status_sched,
                        "note": note_sched.strip(),
                    }
                    append_record("schedules", new_sched)
                    st.success("✅ Đã thêm lịch dạy.")
                    st.rerun()

        st.markdown("### 📋 Danh sách lịch dạy")
        if schedules.empty:
            st.info("Chưa có lịch dạy.")
        else:
            school_map = dict(zip(schools["id"].astype(str), schools["name"]))
            class_map = dict(zip(classes["id"].astype(str), classes["class_name"]))

            display_sched = schedules.copy()
            display_sched["Trường"] = display_sched["school_id"].astype(str).map(school_map)
            display_sched["Lớp"] = display_sched["class_id"].astype(str).map(class_map)
            display_sched["GV1"] = display_sched["teacher1"].map(lambda x: teacher_name(x, teachers))
            display_sched["GV2"] = display_sched["teacher2"].map(lambda x: teacher_name(x, teachers))
            display_sched["GV3"] = display_sched["teacher3"].map(lambda x: teacher_name(x, teachers))

            st.dataframe(
                display_sched[
                    [
                        "id",
                        "day",
                        "Trường",
                        "Lớp",
                        "start_time",
                        "end_time",
                        "GV1",
                        "GV2",
                        "GV3",
                        "status",
                        "note",
                    ]
                ].rename(
                    columns={
                        "id": "ID",
                        "day": "Ngày",
                        "start_time": "Bắt đầu",
                        "end_time": "Kết thúc",
                        "status": "Trạng thái",
                        "note": "Ghi chú",
                    }
                ),
                width="stretch",
                hide_index=True,
            )

            st.markdown("### 🗑️ Xóa lịch dạy")
            sched_opts = {
                f'ID {row["id"]} - {row["day"]} ({row["start_time"]}-{row["end_time"]})': row["id"]
                for _, row in schedules.iterrows()
            }
            selected_sched = st.selectbox("Chọn lịch cần xóa", list(sched_opts.keys()), key="delete_sched")
            if st.button("🗑️ Xóa lịch dạy đã chọn", width="stretch"):
                delete_record("schedules", sched_opts[selected_sched])
                st.success("✅ Đã xóa lịch dạy.")
                st.rerun()


# =========================================================
# GIÁO VIÊN
# =========================================================

elif page == "👨‍🏫 Giáo viên":

    st.markdown("## 👨‍🏫 Quản lý giáo viên")

    with st.form("teacher_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            code = st.text_input("Mã GV")
            name = st.text_input("Họ và tên")
            phone = st.text_input("Số điện thoại")

        with c2:
            role = st.selectbox(
                "Vai trò",
                ["Trưởng nhóm", "Giáo viên", "Dự phòng"],
            )
            status = st.selectbox(
                "Trạng thái",
                ["Đang làm", "Tạm nghỉ", "Nghỉ"],
            )
            note = st.text_input("Ghi chú")

        save = st.form_submit_button("➕ Thêm giáo viên", width="stretch")

    if save:
        code = code.strip()
        name = name.strip()

        if not code or not name:
            st.error("Mã GV và họ tên là bắt buộc.")
        elif code.casefold() in teachers["code"].astype(str).str.casefold().tolist():
            st.error("Mã giáo viên đã tồn tại.")
        else:
            append_record(
                "teachers",
                {
                    "id": next_id(teachers),
                    "code": code,
                    "name": name,
                    "phone": phone.strip(),
                    "role": role,
                    "status": status,
                    "note": note.strip(),
                },
            )
            st.success("✅ Đã thêm giáo viên.")
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
        width="stretch",
        hide_index=True,
    )

    st.markdown("### 🗑️ Xóa giáo viên")

    if not teachers.empty:
        options = {
            f'{row["code"]} — {row["name"]}': row["id"]
            for _, row in teachers.iterrows()
        }

        selected = st.selectbox(
            "Chọn giáo viên cần xóa",
            list(options.keys()),
            key="delete_teacher",
        )

        if st.button("🗑️ Xóa giáo viên đã chọn", width="stretch"):
            teacher_id = options[selected]
            teacher_row = teachers[
                teachers["id"].astype(str) == str(teacher_id)
            ].iloc[0]
            teacher_code = str(teacher_row["code"])

            used_schedule = False
            if not schedules.empty:
                used_schedule = (
                    schedules[["teacher1", "teacher2", "teacher3"]]
                    .astype(str)
                    .eq(teacher_code)
                    .any()
                    .any()
                )

            used_attendance = (
                not attendance.empty
                and (attendance["teacher_code"].astype(str) == teacher_code).any()
            )

            used_kpi = (
                not kpi.empty
                and (kpi["teacher_code"].astype(str) == teacher_code).any()
            )

            if used_schedule or used_attendance or used_kpi:
                st.error("Không thể xóa giáo viên vì đã có dữ liệu liên quan.")
            else:
                delete_record("teachers", teacher_id)
                st.success("✅ Đã xóa giáo viên.")
                st.rerun()


# =========================================================
# ĐIỂM DANH GV
# =========================================================

elif page == "📋 Điểm danh GV":

    st.markdown("## 📋 Điểm danh giáo viên")

    if teachers.empty:
        st.warning("Chưa có giáo viên để điểm danh.")
    else:
        teacher_opts = {
            f'{row["code"]} - {row["name"]}': row["code"]
            for _, row in teachers.iterrows()
        }

        with st.form("attendance_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                att_day = st.date_input("Ngày điểm danh", date.today())
                teacher_sel = st.selectbox("Giáo viên", list(teacher_opts.keys()))
                present_val = st.selectbox("Có mặt", ["Có", "Vắng"])

            with c2:
                ontime_val = st.selectbox("Đúng giờ", ["Đúng giờ", "Đi trễ"])
                fullshift_val = st.selectbox("Đủ ca", ["Đủ ca", "Thiếu ca"])
                att_note = st.text_input("Ghi chú")

            save_att = st.form_submit_button("💾 Lưu điểm danh", width="stretch")

        if save_att:
            t_code = teacher_opts[teacher_sel]
            new_att = {
                "id": next_id(attendance),
                "day": att_day.strftime("%d/%m/%Y"),
                "teacher_code": t_code,
                "present": present_val,
                "on_time": ontime_val,
                "full_shift": fullshift_val,
                "note": att_note.strip(),
            }
            append_record("attendance", new_att)
            st.success("✅ Đã ghi nhận điểm danh.")
            st.rerun()

        st.markdown("### 📋 Lịch sử điểm danh")
        if attendance.empty:
            st.info("Chưa có dữ liệu điểm danh.")
        else:
            disp_att = attendance.copy()
            disp_att["Tên GV"] = disp_att["teacher_code"].map(lambda x: teacher_name(x, teachers))

            st.dataframe(
                disp_att[
                    [
                        "id",
                        "day",
                        "teacher_code",
                        "Tên GV",
                        "present",
                        "on_time",
                        "full_shift",
                        "note",
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
                width="stretch",
                hide_index=True,
            )

            st.markdown("### 🗑️ Xóa dòng điểm danh")
            att_opts = {
                f'ID {row["id"]} - {row["day"]} - {row["teacher_code"]}': row["id"]
                for _, row in attendance.iterrows()
            }
            sel_att = st.selectbox("Chọn dòng cần xóa", list(att_opts.keys()), key="delete_att")
            if st.button("🗑️ Xóa điểm danh đã chọn", width="stretch"):
                delete_record("attendance", att_opts[sel_att])
                st.success("✅ Đã xóa dòng điểm danh.")
                st.rerun()


# =========================================================
# KPI & TIỀN CÔNG
# =========================================================

elif page == "📊 KPI & tiền công":

    st.markdown("## 📊 KPI & Tiền công")

    if teachers.empty:
        st.warning("Chưa có dữ liệu giáo viên.")
    else:
        teacher_opts = {
            f'{row["code"]} - {row["name"]}': row["code"]
            for _, row in teachers.iterrows()
        }

        with st.form("kpi_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                kpi_month = st.text_input("Tháng (MM/YYYY)", date.today().strftime("%m/%Y"))
                kpi_teacher = st.selectbox("Giáo viên", list(teacher_opts.keys()))

            with c2:
                kpi_deduction = st.number_input("Khấu trừ / Phạt (VNĐ)", min_value=0, step=50000)
                kpi_note = st.text_input("Ghi chú KPI")

            save_kpi = st.form_submit_button("💾 Lưu ghi nhận KPI", width="stretch")

        if save_kpi:
            t_code = teacher_opts[kpi_teacher]
            new_kpi = {
                "id": next_id(kpi),
                "month": kpi_month.strip(),
                "teacher_code": t_code,
                "deduction": str(kpi_deduction),
                "note": kpi_note.strip(),
            }
            append_record("kpi", new_kpi)
            st.success("✅ Đã ghi nhận KPI/Khấu trừ.")
            st.rerun()

        st.markdown("### 📋 Bảng tổng hợp KPI & Khấu trừ")
        if kpi.empty:
            st.info("Chưa có ghi nhận KPI.")
        else:
            disp_kpi = kpi.copy()
            disp_kpi["Tên GV"] = disp_kpi["teacher_code"].map(lambda x: teacher_name(x, teachers))
            disp_kpi["Khấu trừ"] = disp_kpi["deduction"].map(money)

            st.dataframe(
                disp_kpi[
                    [
                        "id",
                        "month",
                        "teacher_code",
                        "Tên GV",
                        "Khấu trừ",
                        "note",
                    ]
                ].rename(
                    columns={
                        "id": "ID",
                        "month": "Tháng",
                        "teacher_code": "Mã GV",
                        "note": "Ghi chú",
                    }
                ),
                width="stretch",
                hide_index=True,
            )

            st.markdown("### 🗑️ Xóa ghi nhận KPI")
            kpi_opts = {
                f'ID {row["id"]} - Tháng {row["month"]} - {row["teacher_code"]}': row["id"]
                for _, row in kpi.iterrows()
            }
            sel_kpi = st.selectbox("Chọn dòng cần xóa", list(kpi_opts.keys()), key="delete_kpi")
            if st.button("🗑️ Xóa ghi nhận KPI", width="stretch"):
                delete_record("kpi", kpi_opts[sel_kpi])
                st.success("✅ Đã xóa dòng KPI.")
                st.rerun()


# =========================================================
# SỰ CỐ
# =========================================================

elif page == "⚠️ Sự cố":

    st.markdown("## ⚠️ Quản lý sự cố")

    with st.form("incident_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            inc_day = st.date_input("Ngày xảy ra", date.today())
            inc_school = st.text_input("Trường")
            inc_class = st.text_input("Lớp")
            inc_student = st.text_input("Tên học sinh")

        with c2:
            inc_desc = st.text_area("Mô tả sự cố")
            inc_teacher = st.text_input("GV liên quan")
            inc_handling = st.text_area("Biện pháp xử lý")
            inc_level = st.selectbox("Mức độ", ["Nhẹ", "Trung bình", "Nghiêm trọng"])
            inc_status = st.selectbox("Trạng thái", ["Mới", "Đang xử lý", "Đã xong"])
            inc_note = st.text_input("Ghi chú")

        save_inc = st.form_submit_button("💾 Báo cáo sự cố", width="stretch")

    if save_inc:
        new_inc = {
            "id": next_id(incidents),
            "day": inc_day.strftime("%d/%m/%Y"),
            "school": inc_school.strip(),
            "class_name": inc_class.strip(),
            "student": inc_student.strip(),
            "incident": inc_desc.strip(),
            "teacher": inc_teacher.strip(),
            "handling": inc_handling.strip(),
            "level": inc_level,
            "status": inc_status,
            "note": inc_note.strip(),
        }
        append_record("incidents", new_inc)
        st.success("✅ Đã báo cáo sự cố.")
        st.rerun()

    st.markdown("### 📋 Danh sách sự cố")
    if incidents.empty:
        st.info("Chưa có sự cố nào được ghi nhận.")
    else:
        st.dataframe(
            incidents.rename(
                columns={
                    "id": "ID",
                    "day": "Ngày",
                    "school": "Trường",
                    "class_name": "Lớp",
                    "student": "Học sinh",
                    "incident": "Mô tả sự cố",
                    "teacher": "GV liên quan",
                    "handling": "Cách xử lý",
                    "level": "Mức độ",
                    "status": "Trạng thái",
                    "note": "Ghi chú",
                }
            ),
            width="stretch",
            hide_index=True,
        )

        st.markdown("### 🗑️ Xóa sự cố")
        inc_opts = {
            f'ID {row["id"]} - {row["day"]} - {row["school"]} - {row["student"]}': row["id"]
            for _, row in incidents.iterrows()
        }
        sel_inc = st.selectbox("Chọn sự cố cần xóa", list(inc_opts.keys()), key="delete_inc")
        if st.button("🗑️ Xóa sự cố đã chọn", width="stretch"):
            delete_record("incidents", inc_opts[sel_inc])
            st.success("✅ Đã xóa ghi nhận sự cố.")
            st.rerun()


# =========================================================
# DỮ LIỆU
# =========================================================

elif page == "💾 Dữ liệu":

    st.markdown("## 💾 Quản lý & Xuất dữ liệu")

    st.markdown("### 📥 Tải dữ liệu về máy (Excel)")

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for s_name in SHEETS:
            df_s = get_data(s_name)
            df_s.to_excel(writer, sheet_name=s_name, index=False)

    st.download_button(
        label="📥 Tải toàn bộ dữ liệu Excel (.xlsx)",
        data=output.getvalue(),
        file_name=f"du_lieu_day_boi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )

    st.divider()

    st.markdown("### 🔄 Xóa bộ nhớ đệm (Cache)")

    if st.button("🧹 Xóa cache & tải lại", width="stretch"):
        refresh_data()
        st.success("✅ Đã dọn dẹp bộ nhớ đệm.")
        st.rerun()