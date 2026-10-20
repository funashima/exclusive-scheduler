#!/usr/bin/env python3
"""Offline, single-file PyQt6 scheduler for exclusive resource allocation.

Participants express three ranked preferences over date-specific time
slots. An exact interval-scheduling dynamic program assigns at most one
non-overlapping requested slot per person, prioritizing coverage, first
and second choices, then earlier times. Schedules are stored locally as
UTF-8 JSON; no server, database, or network access is required.

The graphical interface is intentionally Japanese-language, while the
implementation's documentation is in English.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime

from PyQt6.QtCore import Qt, QDate, QTime
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QApplication, QAbstractItemView, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QMainWindow, QMessageBox, QPushButton, QTableWidget,
    QDialog, QDialogButtonBox, QDateEdit, QTimeEdit, QFormLayout,
    QTableWidgetItem, QVBoxLayout, QWidget, QSpinBox,
)



# ----------------- Embedded exact assignment solver -----------------

@dataclass(frozen=True)
class Solution:
    """Immutable result returned by :func:`solve`.

    Attributes:
        assignments: Pairs of ``(slot_index, person_index)`` referring to the
        original input arrays, not the internally sorted event order.
        unassigned: Indices of people for whom no feasible slot was allocated.
        counts: Tuple ``(assigned_count, first_choice_count,
        second_choice_count)``; third-choice count is the remainder."""
    assignments: tuple[tuple[int, int], ...]  # Original slot index and participant index
    unassigned: tuple[int, ...]
    counts: tuple[int, int, int]  # Total assigned, first-choice, second-choice


def _bounds(date: str, slot: str) -> tuple[datetime, datetime]:
    """Convert an ISO date and a displayed time range into datetime bounds.

    Args:
        date: Date as ``YYYY-MM-DD``; an empty string represents legacy,
        undated records and is mapped to year 1 for comparisons.
        slot: Inclusive start and exclusive end in ``HH:MM–HH:MM`` format.

    Returns:
        A pair ``(start, end)`` of naive datetimes.

    Raises:
        ValueError: If the date or time range cannot be parsed."""
    a, b = slot.replace("-", "–").split("–", 1)
    day = date or "0001-01-01"  # Undated legacy entries use the earliest representable date.
    return (datetime.fromisoformat(f"{day}T{a.strip()}:00"),
            datetime.fromisoformat(f"{day}T{b.strip()}:00"))


def solve(people: list[str], dates: list[str], slots: list[str],
          wishes: list[list[int]]) -> Solution:
    """Compute an exact, non-overlapping assignment of preferred time slots.
    
    Each person may receive at most one slot. A slot may be given to at most
    one person, and distinct slots with overlapping time intervals cannot
    both be allocated. Unrequested slots are never eligible.
    
    The dynamic program processes intervals by finishing time and uses a bit
    mask to track assigned people. Candidate solutions are ordered
    lexicographically by (1) number assigned, (2) number of first-choice
    assignments, (3) number of second-choice assignments, and (4) the
    negative sum of chronological start-order indices. Thus third choices
    are considered only after the higher priorities, and earlier times
    break ties across complete solutions rather than person by person.
    
    Args:
        people: Participant names, in column order (at most 15).
        dates: ISO date per row; must align with ``slots`` and ``wishes``.
        slots: Display time interval per row, such as ``09:00–10:00``.
        wishes: Preference matrix indexed ``[slot][person]`` with
            0 = no request, 1 = first, 2 = second, 3 = third choice.
    
    Returns:
        A :class:`Solution` with original row indices and summary counts.
        When full assignment is impossible, returns an optimal partial one.
    
    Raises:
        ValueError: For excessive participant count or invalid input shapes,
            preference values, dates, or intervals.
    
    Notes:
        The state space grows exponentially with the number of people.
        All comparisons use naive local datetimes; overnight intervals are
        not supported."""
    n = len(people)
    if n > 15:
        raise ValueError("厳密ソルバの対象は15人までです（計算量は人数に対して指数的です）。")
    if not (len(dates) == len(slots) == len(wishes)):
        raise ValueError("枠データの長さが一致しません。")
    events = []
    for i, (d, s, preferences) in enumerate(zip(dates, slots, wishes)):
        if len(preferences) != n or any(type(x) is not int or x not in (0, 1, 2, 3) for x in preferences):
            raise ValueError("希望データが不正です。")
        if not any(preferences):
            continue
        start, end = _bounds(d, s)
        if start >= end:
            raise ValueError("時間枠の開始・終了が不正です。")
        events.append((start, end, i, preferences))
    events.sort(key=lambda x: (x[1], x[0], x[2]))
    # Tie-breaking uses chronological start order, not finish order.
    order = {event[2]: idx for idx, event in enumerate(sorted(events, key=lambda x: (x[0], x[1], x[2])))}
    # dp[j] maps participant bitmasks to optimal solutions for events 1..j.
    # Score: (assigned, first choices, second choices, -sum(start ranks)).
    dp: list[dict[int, tuple[tuple[int, int, int, int], tuple[tuple[int, int], ...]]]] = [
        {0: ((0, 0, 0, 0), ())}
    ]
    for j, (start, _end, idx, prefs) in enumerate(events, start=1):
        available = 0
        for k in range(j - 1, 0, -1):
            if events[k - 1][1] <= start:
                available = k
                break
        current = dp[-1].copy()
        for mask, (score, assignments) in dp[available].items():
            for person, rank in enumerate(prefs):
                if rank == 0 or (mask & (1 << person)):
                    continue
                next_mask = mask | (1 << person)
                candidate = (score[0] + 1, score[1] + (rank == 1),
                             score[2] + (rank == 2), score[3] - order[idx])
                existing = current.get(next_mask)
                if existing is None or candidate > existing[0]:
                    current[next_mask] = candidate, assignments + ((idx, person),)
        dp.append(current)
    best_mask, (best_score, choices) = max(dp[-1].items(), key=lambda item: (item[1][0], -item[0]))
    return Solution(tuple(sorted(choices)), tuple(i for i in range(n) if not (best_mask & (1 << i))),
                    best_score[:3])

# ----------------- PyQt6 user interface -----------------

COLORS = {
    "unused": QColor("#eeeeee"),
    "exclusive": QColor("#c5edcb"),
    "conflict": QColor("#ffe0ad"),
    "normal": QColor("#ffffff"),
    "solved": QColor("#8ec7fa"),
}


class SlotDialog(QDialog):
    """Dialog for adding or editing one date-specific time interval.
    
    Uses calendar and time controls and rejects intervals whose end is not
    strictly later than their start on the selected date."""

    def __init__(self, parent=None, date="", slot="09:00–10:00"):
        """Initialize the single-slot editor.

        Args:
            parent: Optional Qt parent widget.
            date: Initial ISO date, or an empty string to use today.
            slot: Initial displayed time range (``HH:MM–HH:MM``)."""
        super().__init__(parent)
        self.setWindowTitle("日付・時間枠")
        layout = QFormLayout(self)
        self.date_input = QDateEdit()
        self.date_input.setCalendarPopup(True)
        d = QDate.fromString(date, "yyyy-MM-dd") if date else QDate.currentDate()
        self.date_input.setDate(d if d.isValid() else QDate.currentDate())
        self.start_input = QTimeEdit()
        self.end_input = QTimeEdit()
        for widget in (self.start_input, self.end_input):
            widget.setDisplayFormat("HH:mm")
        try:
            start, end = slot.replace("-", "–").split("–", 1)
            t1 = QTime.fromString(start.strip(), "HH:mm")
            t2 = QTime.fromString(end.strip(), "HH:mm")
        except ValueError:
            t1, t2 = QTime(9, 0), QTime(10, 0)
        self.start_input.setTime(t1 if t1.isValid() else QTime(9, 0))
        self.end_input.setTime(t2 if t2.isValid() else QTime(10, 0))
        layout.addRow("日付", self.date_input)
        layout.addRow("開始", self.start_input)
        layout.addRow("終了", self.end_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def validate_and_accept(self):
        """Accept only a strictly positive, same-day interval."""
        if self.start_input.time() >= self.end_input.time():
            QMessageBox.warning(self, "時刻の確認", "終了時刻は開始時刻より後にしてください。")
            return
        self.accept()

    def values(self):
        """Return the chosen ``(ISO_date, formatted_interval)`` pair."""
        return (self.date_input.date().toString("yyyy-MM-dd"),
                f"{self.start_input.time().toString('HH:mm')}–{self.end_input.time().toString('HH:mm')}")


class BatchSlotDialog(QDialog):
    """Dialog for generating equal-length slots within one day.
    
    The requested time span must be divisible by the interval length;
    partial last slots and overnight ranges are deliberately disallowed."""

    def __init__(self, parent=None):
        """Create date, start/end-time, and step-in-minutes controls."""
        super().__init__(parent)
        self.setWindowTitle("日付・時間枠の一括追加")
        layout = QFormLayout(self)
        self.date_input = QDateEdit()
        self.date_input.setCalendarPopup(True)
        self.date_input.setDisplayFormat("MM/dd (yyyy)")
        self.date_input.setDate(QDate.currentDate())
        self.start_input = QTimeEdit()
        self.end_input = QTimeEdit()
        for widget in (self.start_input, self.end_input):
            widget.setDisplayFormat("HH:mm")
        self.start_input.setTime(QTime(9, 0))
        self.end_input.setTime(QTime(17, 0))
        self.interval_input = QSpinBox()
        self.interval_input.setRange(1, 1440)
        self.interval_input.setValue(30)
        self.interval_input.setSuffix(" 分")
        layout.addRow("日付 (月/日)", self.date_input)
        layout.addRow("開始時刻", self.start_input)
        layout.addRow("終了時刻", self.end_input)
        layout.addRow("分割間隔", self.interval_input)
        self.preview = QLabel()
        self.start_input.timeChanged.connect(self.update_preview)
        self.end_input.timeChanged.connect(self.update_preview)
        self.interval_input.valueChanged.connect(self.update_preview)
        layout.addRow("追加予定", self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self.update_preview()

    def ranges(self):
        """Return ordered equal-duration intervals, or an empty list if invalid.

        Intervals use ``HH:MM–HH:MM`` strings; the finish time is exclusive."""
        start = self.start_input.time().hour() * 60 + self.start_input.time().minute()
        end = self.end_input.time().hour() * 60 + self.end_input.time().minute()
        step = self.interval_input.value()
        if end <= start or (end - start) % step:
            return []
        def fmt(minutes):
            return f"{minutes // 60:02d}:{minutes % 60:02d}"
        return [f"{fmt(m)}–{fmt(m + step)}" for m in range(start, end, step)]

    def update_preview(self, *_args):
        """Update the live count of intervals the current inputs would create."""
        rows = self.ranges()
        self.preview.setText(f"{len(rows)} 枠" if rows else "範囲または分割間隔を確認してください")

    def validate_and_accept(self):
        """Validate divisibility and positive duration before closing."""
        start = self.start_input.time()
        end = self.end_input.time()
        if end <= start:
            QMessageBox.warning(self, "時間帯の確認", "終了時刻は開始時刻より後にしてください。")
        elif not self.ranges():
            QMessageBox.warning(self, "分割間隔の確認", "指定時間帯が分割間隔で割り切れるようにしてください。端数の枠は作りません。")
        else:
            self.accept()

    def values(self):
        """Return the selected ISO date and all generated time ranges."""
        return self.date_input.date().toString("yyyy-MM-dd"), self.ranges()


class Scheduler(QMainWindow):
    """Main window for local preference entry and resource allocation.

    The aligned ``dates``, ``slots`` and ``wishes`` lists form the schedule.
    Sorting must move entire rows together so preference ownership is not
    lost. Changes invalidate previously computed (non-persisted) solutions."""
    def __init__(self):
        """Initialize the local schedule, table, actions, and file state."""
        super().__init__()
        self.setWindowTitle("時間希望・独占枠チェッカー")
        self.resize(1020, 650)
        self.path: Path | None = None
        self.changed = False
        self.people = ["自分", "Aさん", "Bさん"]
        self.slots = ["09:00–10:00", "10:00–11:00", "11:00–12:00", "13:00–14:00", "14:00–15:00"]
        self.dates = [QDate.currentDate().toString("yyyy-MM-dd")] * len(self.slots)
        self.wishes: list[list[int]] = [[0] * len(self.people) for _ in self.slots]
        self.solution = None
        self._updating = False

        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        explanation = QLabel("セルをダブルクリックして 空欄 → ◎(第1) → ○(第2) → △(第3) → 空欄。ソルバは希望者に重複しない時間を1枠ずつ割り当てます。")
        explanation.setWordWrap(True)
        vbox.addWidget(explanation)
        self.table = QTableWidget()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.toggle_wish)
        vbox.addWidget(self.table)

        actions = QHBoxLayout()
        for label, callback in [
            ("人を追加", self.add_person), ("人を削除", self.remove_person),
            ("日付・時間枠を追加", self.add_slot), ("一括追加", self.add_batch_slots), ("時間枠を削除", self.remove_slot),
            ("日付・時間枠を編集", self.edit_slot),
            ("ソルバを実行", self.run_solver),
        ]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        vbox.addLayout(actions)
        self.report = QLabel()
        self.report.setWordWrap(True)
        self.report.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        vbox.addWidget(self.report)
        self._create_menu()
        self.refresh()

    def _create_menu(self):
        """Add New, Open, Save, and Save As menu actions and shortcuts."""
        menu = self.menuBar().addMenu("ファイル")
        for title, callback, shortcut in [
            ("新規", self.new_file, "Ctrl+N"),
            ("開く...", self.open_file, "Ctrl+O"),
            ("保存", self.save, "Ctrl+S"),
            ("名前を付けて保存...", self.save_as, "Ctrl+Shift+S"),
        ]:
            act = QAction(title, self)
            act.setShortcut(shortcut)
            act.triggered.connect(callback)
            menu.addAction(act)

    def mark_changed(self):
        """Mark data dirty and invalidate any earlier solver result."""
        self.solution = None
        self.changed = True
        self.update_title()

    def update_title(self):
        """Show the current filename and an asterisk for unsaved changes."""
        name = self.path.name if self.path else "未保存"
        self.setWindowTitle(f"時間希望・独占枠チェッカー — {name}{' *' if self.changed else ''}")

    def toggle_wish(self, row, col):
        """Cycle a participant cell through none, first, second, and third.

        Args:
            row: Schedule row index.
            col: Table column index, including date and time columns."""
        if col < 2 or col == len(self.people) + 2:
            return
        self.wishes[row][col - 2] = (self.wishes[row][col - 2] + 1) % 4
        self.mark_changed()
        self.refresh()

    def sort_slots(self):
        """Sort dates, slots, and corresponding preference rows together.
        
        Sort keys are ISO date, interval start time, then end time. Legacy
        undated rows sort before dated rows."""
        if len(self.slots) < 2:
            return
        # Undated legacy entries precede dated rows.
        rows = sorted(
            zip(self.dates, self.slots, self.wishes),
            key=lambda row: (row[0], *self._slot_order(row[1])),
        )
        self.dates, self.slots, self.wishes = map(list, zip(*rows))

    @staticmethod
    def _slot_order(slot):
        """Produce numeric (start-hour, minute, end-hour, minute) sort keys."""
        parts = slot.replace("-", "–").split("–", 1)
        try:
            return tuple(int(x) for part in parts for x in part.strip().split(":"))
        except ValueError:
            return (99, 99, 99, 99)

    def refresh(self):
        """Rebuild the table and exclusive/conflict summary from current data.

        Gray means no requests, green means one requester, orange means two or
        more requesters, and blue marks an actual solver assignment. This
        visual exclusivity summary does not itself solve allocation conflicts."""
        self.sort_slots()
        self._updating = True
        self.table.setRowCount(len(self.slots))
        self.table.setColumnCount(len(self.people) + 3)
        self.table.setHorizontalHeaderLabels(["日付", "時間枠", *self.people, "判定"])
        exclusive_by_person: dict[str, list[str]] = {name: [] for name in self.people}
        conflict_count = 0
        for row, slot in enumerate(self.slots):
            users = [i for i, wish in enumerate(self.wishes[row]) if wish > 0]
            if len(users) == 0:
                status, bg = "対象外（希望なし）", COLORS["unused"]
            elif len(users) == 1:
                owner = self.people[users[0]]
                status, bg = f"独占：{owner}", COLORS["exclusive"]
                exclusive_by_person[owner].append(f"{self.dates[row]} {slot}".strip())
            else:
                status, bg = "競合：" + "、".join(self.people[i] for i in users), COLORS["conflict"]
                conflict_count += 1
            vals = [self.dates[row], slot, *[{0: "", 1: "◎", 2: "○", 3: "△"}[x] for x in self.wishes[row]], status]
            for col, value in enumerate(vals):
                cell = QTableWidgetItem(value)
                if self.solution is not None and (row, col - 2) in self.solution.assignments:
                    cell.setBackground(COLORS["solved"])
                    cell.setToolTip("ソルバが割り当てた時間枠")
                else:
                    cell.setBackground(bg)
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if 2 <= col <= len(self.people) + 1:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, cell)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        detail = "\n".join(f"{name}: {('、'.join(times) if times else 'なし')}" for name, times in exclusive_by_person.items())
        self.report.setText(f"【希望者が1人の枠（順位不問）】\n{detail}\n競合枠：{conflict_count} 件（希望0人の枠は集計対象外）")
        self._updating = False

    def add_person(self):
        """Prompt for a unique participant and add an empty preference column."""
        name, ok = QInputDialog.getText(self, "人を追加", "名前")
        name = name.strip()
        if not ok or not name:
            return
        if name in self.people:
            QMessageBox.warning(self, "重複", "同じ名前が存在します。")
            return
        self.people.append(name)
        for row in self.wishes:
            row.append(0)
        self.mark_changed()
        self.refresh()

    def remove_person(self):
        """Remove a selected participant and their preferences after confirmation."""
        if len(self.people) <= 1:
            QMessageBox.warning(self, "削除不可", "最低1人は必要です。")
            return
        name, ok = QInputDialog.getItem(self, "人を削除", "削除する人", self.people, 0, False)
        if not ok:
            return
        if QMessageBox.question(self, "確認", f"{name} とその希望を削除しますか？") != QMessageBox.StandardButton.Yes:
            return
        index = self.people.index(name)
        self.people.pop(index)
        for row in self.wishes:
            row.pop(index)
        self.mark_changed()
        self.refresh()

    def add_slot(self):
        """Insert a manually specified date/time row unless it already exists."""
        dialog = SlotDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        date, slot = dialog.values()
        if any(d == date and t == slot for d, t in zip(self.dates, self.slots)):
            QMessageBox.warning(self, "重複", "同じ日付・時間枠が既に存在します。")
            return
        self.dates.append(date)
        self.slots.append(slot)
        self.wishes.append([0] * len(self.people))
        self.mark_changed()
        self.refresh()

    def add_batch_slots(self):
        """Append generated same-day slots, skipping exact duplicate rows."""
        dialog = BatchSlotDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        date, slots = dialog.values()
        existing = set(zip(self.dates, self.slots))
        new_slots = [slot for slot in slots if (date, slot) not in existing]
        for slot in new_slots:
            self.dates.append(date)
            self.slots.append(slot)
            self.wishes.append([0] * len(self.people))
        if new_slots:
            self.mark_changed()
            self.refresh()
        skipped = len(slots) - len(new_slots)
        QMessageBox.information(self, "一括追加の結果", f"追加: {len(new_slots)} 枠\n既存と重複してスキップ: {skipped} 枠")

    def remove_slot(self):
        """Remove the selected time-slot row after confirmation."""
        row = self.table.currentRow()
        if not 0 <= row < len(self.slots):
            QMessageBox.information(self, "時間枠を削除", "削除する行を選択してください。")
            return
        if QMessageBox.question(self, "削除の確認", f"{self.dates[row]} {self.slots[row]} を削除しますか？") != QMessageBox.StandardButton.Yes:
            return
        self.slots.pop(row)
        self.dates.pop(row)
        self.wishes.pop(row)
        self.mark_changed()
        self.refresh()

    def edit_slot(self):
        """Edit the selected date/time without losing its preference cells."""
        row = self.table.currentRow()
        if not 0 <= row < len(self.slots):
            QMessageBox.information(self, "日付・時間枠を編集", "編集する行を選択してください。")
            return
        dialog = SlotDialog(self, self.dates[row], self.slots[row])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        date, slot = dialog.values()
        if any(i != row and d == date and t == slot for i, (d, t) in enumerate(zip(self.dates, self.slots))):
            QMessageBox.warning(self, "重複", "同じ日付・時間枠が既に存在します。")
            return
        self.dates[row], self.slots[row] = date, slot
        self.mark_changed()
        self.refresh()

    def run_solver(self):
        """Find the optimal assignment and display allocation and missing people.

        Solver results are displayed in blue and in a message dialog. They
        are not included in the persisted JSON data."""
        try:
            result = solve(self.people, self.dates, self.slots, self.wishes)
        except (ValueError, OverflowError) as exc:
            QMessageBox.warning(self, "ソルバの実行失敗", str(exc))
            return
        self.solution = result
        self.refresh()
        assignments = sorted(result.assignments, key=lambda pair: pair[1])
        lines = [f"{self.people[p]}：{self.dates[s]} {self.slots[s]}  "
                 f"({['', '◎', '○', '△'][self.wishes[s][p]]})"
                 for s, p in assignments]
        if result.unassigned:
            lines.append("未割当：" + "、".join(self.people[p] for p in result.unassigned))
        if not lines:
            lines.append("希望が入力されていません。")
        header = ("全員に割り当てられました" if not result.unassigned else
                  "全員への割当は不可能です。割当人数を最大化しました")
        summary = (f"{header}\n"
                   f"割当 {result.counts[0]}/{len(self.people)}人・"
                   f"◎ {result.counts[1]}件・○ {result.counts[2]}件・"
                   f"△ {result.counts[0] - result.counts[1] - result.counts[2]}件\n\n"
                   + "\n".join(lines))
        self.report.setText("【ソルバ結果（青色セルが確定割当）】\n" + summary)
        QMessageBox.information(self, "ソルバ結果", summary)

    def payload(self):
        """Build the JSON-serializable version 3 schedule dictionary."""
        return {"version": 3, "people": self.people, "dates": self.dates, "slots": self.slots, "wishes": self.wishes}

    def check_unsaved(self):
        """Ask whether to save, discard, or cancel when data is dirty.

        Returns:
            ``True`` if the caller may continue; ``False`` on cancellation or
            when saving was requested but failed."""
        if not self.changed:
            return True
        choice = QMessageBox.question(self, "未保存の変更", "変更を保存しますか？", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self.save()
        return True

    def new_file(self):
        """Discard or save current edits, then start with one empty participant."""
        if not self.check_unsaved():
            return
        self.people, self.slots, self.dates = ["自分"], [], []
        self.wishes = []
        self.solution = None
        self.path, self.changed = None, False
        self.update_title()
        self.refresh()

    def open_file(self):
        """Load and validate a JSON schedule and migrate legacy boolean wishes.

        Legacy files without a ``dates`` field get empty date strings.
        Invalid input is reported without replacing the current schedule."""
        if not self.check_unsaved():
            return
        filename, _ = QFileDialog.getOpenFileName(self, "JSONを開く", "", "JSON (*.json)")
        if not filename:
            return
        try:
            data = json.loads(Path(filename).read_text(encoding="utf-8"))
            people, slots, wishes = data["people"], data["slots"], data["wishes"]
            dates = data.get("dates", [""] * len(slots))
            if (not isinstance(people, list) or not people or not all(isinstance(x, str) and x.strip() for x in people)
                or len(set(people)) != len(people) or not isinstance(slots, list)
                or not all(isinstance(x, str) for x in slots) or not isinstance(wishes, list)
                or not isinstance(dates, list) or len(dates) != len(slots)
                or not all(isinstance(d, str) and (not d or (QDate.fromString(d, "yyyy-MM-dd").isValid() and QDate.fromString(d, "yyyy-MM-dd").toString("yyyy-MM-dd") == d)) for d in dates)
                or len(slots) != len(wishes)
                or not all(isinstance(row, list) and len(row) == len(people) and all(type(v) is bool or (type(v) is int and v in (0, 1, 2, 3)) for v in row) for row in wishes)):
                raise ValueError("people, slots, wishes の構造が正しくありません")
        except (OSError, ValueError, KeyError, TypeError) as e:
            QMessageBox.critical(self, "読込失敗", str(e))
            return
        self.people, self.slots, self.dates, self.wishes = people, slots, dates, [[1 if v is True else 0 if v is False else v for v in row] for row in wishes]
        self.solution = None
        self.path, self.changed = Path(filename), False
        self.update_title()
        self.refresh()

    def save_as(self):
        """Choose an output filename and write the schedule as JSON.

        Returns:
            ``True`` after successful save; ``False`` on cancel or error."""
        filename, _ = QFileDialog.getSaveFileName(self, "名前を付けて保存", str(self.path or "schedule.json"), "JSON (*.json)")
        if not filename:
            return False
        path = Path(filename)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        return self._write(path)

    def save(self):
        """Save to the current path, prompting for a filename if needed."""
        return self._write(self.path) if self.path else self.save_as()

    def _write(self, path: Path):
        """Atomically replace the target file with UTF-8 JSON.

        Writes to a neighboring temporary file first, then replaces the target
        so a failed write does not leave a truncated schedule.

        Args:
            path: Destination path.

        Returns:
            ``True`` on success; ``False`` on an operating-system error."""
        try:
            # Replace atomically to avoid leaving a partially written JSON file.
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(self.payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            QMessageBox.critical(self, "保存失敗", str(e))
            return False
        self.path, self.changed = path, False
        self.update_title()
        return True

    def closeEvent(self, event):
        """Allow closing only after unsaved changes have been resolved."""
        if self.check_unsaved():
            event.accept()
        else:
            event.ignore()


def main():
    """Start the Qt event loop and display the scheduler window."""
    app = QApplication(sys.argv)
    widget = Scheduler()
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
