# Exclusive Scheduler

A **local-only desktop scheduler** for allocating a shared time resource among multiple participants. Built with **Python and PyQt6**, it lets each person express ranked preferences for date-specific time slots and computes a conflict-free allocation. No account, hosted service, database, or network connection is required.

The application has a **Japanese-language GUI**. This README and the Python documentation are in English.

## Features

- Register, rename by removal/re-creation, or remove participants; add, edit, and delete dated time slots.
- Add many slots at once by choosing a date, start/end times, and an interval in minutes. The duration must divide evenly into the specified span.
- Double-click a preference cell to cycle through **blank → ◎ → ○ → △ → blank**.
- Automatically sort rows by **date, start time, and end time**, without changing which preferences belong to each row.
- Distinguish unrequested, uncontested, and contested slots at a glance.
- Calculate an exact, non-overlapping allocation with the built-in solver; display chosen cells in blue.
- Save and reopen editable schedules in local UTF-8 JSON files.
- Run directly from the single `exclusive_scheduler.py` file; no separate solver module is needed.

## Requirements

- Python 3.10 or later (type hints use built-in generic types and `|` unions)
- PyQt6

Tested by Python syntax compilation; complete interactive GUI testing has not been performed in this environment.

## Installation and launch

On Ubuntu or another Linux desktop with Qt support:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install PyQt6
python exclusive_scheduler.py
```

All scheduling data remains on your computer. The solver uses only the Python standard library; PyQt6 is needed for the GUI.

## Quick start

1. Use **Add Person** (`人を追加`) to create participants.
2. Use **Add Date/Time Slot** (`日付・時間枠を追加`) or **Batch Add** (`一括追加`) to define the resource's possible usage periods.
3. Double-click each participant's cell for the relevant row until the preferred rank appears.
4. Click **Run Solver** (`ソルバを実行`) to see the optimal allocation. A dialog summarizes allocations and any unassigned participants.
5. Save the editable schedule using **File → Save** (`ファイル → 保存`, `Ctrl+S`).

Batch-add example: on `2026-10-25`, the range `09:00–12:00` with a `30`-minute interval generates six slots. Identical date/interval rows are skipped rather than overwritten. The start and end times must fall on the same day and the total span must be divisible by the step size; overnight and partial trailing slots are not supported.

## Preferences and colors

| Cell | Meaning | Stored value |
| --- | --- | ---: |
| ◎ | First choice | `1` |
| ○ | Second choice | `2` |
| △ | Third choice | `3` |
| Blank | Not requested; never assign this person to the slot | `0` |

- **Gray:** nobody requested the slot; ignored when optimizing.
- **Green:** exactly one person requested the slot, regardless of rank.
- **Orange:** two or more people requested the slot.
- **Blue:** the solver selected this participant/slot assignment.

Green is only a visualization of *requests*; it is not a guarantee that this slot is compatible with all other selected slots. Actual assignments are indicated in blue.

## Optimization model

A single resource is shared by all participants. The exact solver uses weighted interval scheduling with dynamic programming over subsets of participants:

1. **Maximize the number of participants assigned** (each person receives at most one requested slot).
2. Subject to that, **maximize the number of first-choice (◎) assignments**.
3. Then **maximize second-choice (○) assignments**.
4. Finally, prefer **earlier dated slots** by minimizing the sum of chronological start-order indices across selected slots.

Only requested slots are eligible. The solver never assigns overlapping intervals to different people, **even when the intervals are different rows**. Endpoints may touch: `09:00–10:00` and `10:00–11:00` do not overlap. It may return an optimal *partial* allocation when not everybody can be assigned; unassigned participants appear in the result.

These are **global, lexicographic priorities**. The solver does *not* guarantee the best possible preference rank individually for every participant, nor does it implement a max-min fairness objective. An earlier date/time is a tie-breaker *after* coverage and preference counts, not a higher-priority requirement.

**Limits:** up to **15 participants**. Runtime and memory requirements grow exponentially with participant count. Times are treated as local clock times without timezone/DST modeling. Slots cannot cross midnight. The implementation is intended for modest, manually managed schedules, not large-scale resource planning.

Solver results are **not persisted**; the JSON stores user preferences only. Any change to the schedule clears the previous allocation display. Run the solver again after editing.

## JSON file format

The current format is version 3:

```json
{
  "version": 3,
  "people": ["Me", "Alice"],
  "dates": ["2026-10-21", "2026-10-21"],
  "slots": ["09:00–10:00", "10:00–11:00"],
  "wishes": [[1, 2], [0, 1]]
}
```

Arrays are positionally aligned: `dates[i]`, `slots[i]`, and `wishes[i]` describe one row; `wishes[i][j]` represents participant `people[j]` for that row. Dates use ISO `YYYY-MM-DD`. Slots use 24-hour `HH:MM–HH:MM` strings. JSON is encoded in UTF-8, preserving participant names and preference symbols.

Previous files that store boolean wishes are accepted: `true` becomes first choice (`1`) and `false` becomes unrequested (`0`). Files without a `dates` array are accepted as legacy undated records; supply dates through the editor when necessary.

## Project layout

```text
exclusive_scheduler.py   # GUI, data model, JSON I/O, and exact solver
README.md                # This file
```

## Development notes

The public `solve(people, dates, slots, wishes)` function returns a `Solution` dataclass containing assigned `(slot_index, person_index)` pairs, unassigned person indices, and counts for total, first-choice, and second-choice allocations. Both indices refer to the supplied arrays. The solver does not depend on Qt, though the module imports PyQt6 for the GUI.

The source code includes English docstrings describing the data model, algorithm, UI operations, and persistence behavior. GUI labels and user-facing error messages remain Japanese.

## License

MIT License
