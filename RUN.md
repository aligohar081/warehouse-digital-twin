# RUN.md — setup and commands

Commands for running the app, its tests, the eval CLI, promptfoo and the soak.
Run them from the project root. What the project is: [README.md](README.md).

## 1. Prerequisites

| Tool | Version | For |
|---|---|---|
| Python | 3.14 (what the project's `.venv` runs) | the app, tests, eval CLI and soak |
| Node.js + npm | `^20.20.0 \|\| >=22.22.0` | promptfoo only |
| Groq API key | — | the Groq-judged promptfoo suite and the chat agent; free at [console.groq.com/keys](https://console.groq.com/keys) |
| JavaScriptCore (`jsc`) | built into macOS | the dashboard's JavaScript tests; they skip without it |

## 2. Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # Flask, pytest and PyYAML
```

`./run.sh` does all of that and starts the app. The commands below assume the venv is active.

## 3. Run the app

```bash
WAREHOUSE_PORT=5055 python -m backend.app                                  # the distribution centre (the default)
WAREHOUSE_LAYOUT=classic WAREHOUSE_PORT=5055 python -m backend.app         # the classic floor
WAREHOUSE_LAYOUT=distribution_center WAREHOUSE_PORT=5055 python -m backend.app   # the default, spelled out
```

The dashboard is at `http://127.0.0.1:<port>`; stop with Ctrl+C. `WAREHOUSE_HOST` and `WAREHOUSE_PORT` default to `127.0.0.1` and `5000`. **On macOS the AirPlay Receiver holds port 5000, so use another port.** Each floor's `logs/` and `data/` folders are listed in the [README's quick start](README.md#quick-start).

The distribution centre's shift starts paused. Press **Start** on the Shift panel, or:

```bash
curl -X POST http://127.0.0.1:5055/api/shift/start
```

Inject one fault on demand from the Shift panel's **Inject fault**, or `curl -X POST http://127.0.0.1:5055/api/faults/conveyor_jam`.

## 4. Tests

```bash
python -m pytest -o addopts="" -q                                # the full suite, with its summary line
python -m pytest -o addopts="" -q backend/test_station_jobs.py   # one file
python -m pytest -o addopts="" -q backend/tests.py::test_name    # one test
python -m pytest -o addopts="" -q -k "collision or battery"      # by keyword
```

`pytest.ini` sets `-q`, so `-o addopts=""` is what lets the summary line print. Two failures are expected (section 8). The dashboard's JavaScript tests, `frontend/tests/*.js`, run under macOS's built-in JavaScriptCore (`/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc`) through pytest wrappers that skip when `jsc` is absent; no Node is needed:

```bash
python -m pytest -o addopts="" -q backend/test_floor_model_js.py backend/test_panels_js.py
```

## 5. Eval CLI

Rule-based grading of task logs, no promptfoo and no API (checks: [TRUST_LAYER.md](TRUST_LAYER.md)).

```bash
python -m backend.run_evals                                      # every real logs/tasks/*.json
python -m backend.run_evals logs/tasks/task_006.json             # one file
python -m backend.run_evals --demo                               # the curated logs/eval_examples/
python -m backend.run_evals --stats                              # success rate, assurance coverage, top failing checks
python -m backend.run_evals --out logs/evals/report.json         # also save the full JSON report
python -m backend.run_evals --fail-under 5                       # exit 1 if fewer than 5 tasks pass
python -m backend.run_evals logs/distribution_center/tasks       # the distribution centre's own logs
python -m backend.run_evals logs/eval_examples/multi_embodiment  # the physical checks' pass and fail fixtures
```

## 6. Promptfoo

Three suites (live logs, curated examples, Groq-judged) live in `evals/`; their commands, the Groq key setup and the model choice are in [evals/README.md](evals/README.md). Install once with `npm install -g promptfoo` and call `promptfoo` directly, since `npx promptfoo` re-resolves over the network and can hang for minutes. Run the Groq suite with `--env-file evals/.env -j 1`: its token budget is small, and promptfoo only auto-loads a `.env` from the directory it runs in.

## 7. Soak

```bash
python -m backend.soak                                    # seed 42, pace 2, 20 000 ticks, every risk at 0
python -m backend.soak --ticks 6000 --risk grasp_fail=0.2 # a shorter run with one fault
python -m backend.soak --help                             # --ticks, --seed, --pace, --risk KIND=CHANCE (repeatable)
```

It boots the seeded distribution centre, runs the shift and prints a report: same-layer collisions, failures of the physical checks, orders done per kind, robots in `ERROR`, safety escalations, and the mean and longest tick against the 15 ms budget. `--risk` takes a fault kind or a `CONFIG` risk name ([README.md](README.md#faults)) and a chance from 0 to 1. `backend/test_soak.py` asserts the default run and `backend/test_fault_matrix.py` runs each fault at 0.2.

## 8. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `test_real_task_006_box_conflict_is_caught` and `test_idle_robot_with_a_low_battery_charges_itself` fail | Known and allowed. The first reads `logs/tasks/task_006.json`, which each fresh run appends to (ids restart at `task_001`; Clear logs deletes the files); the second is pre-existing simulator behaviour: the robot's battery runs out before it reaches the charger. Any other failure is yours. |
| The distribution centre's robots stand still | Its shift starts paused: press **Start** on the Shift panel (section 3). |
| The app stops at start with `Unknown robot_class 'ARM'` | A `robot_classes` block in `policies.yaml` replaces the whole roster and lacks `ARM` or `HUMANOID`; `policies.example.yaml` holds the built-in one. |
| `POST /api/state/load` says "This save is of the classic floor, and this twin runs the distribution_center floor" | Each floor loads only its own saves: start the app with the `WAREHOUSE_LAYOUT` the message names. |
| `test_floor_model_js.py` and `test_panels_js.py` are skipped | `jsc` isn't at its macOS path (Linux, say). Expected; run them on a Mac. |
| `pytest` fails with `ModuleNotFoundError: No module named 'yaml'` inside `launch_testing` | An unrelated ROS pytest plugin is auto-loaded: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p no:cacheprovider`. |
| A promptfoo command hangs for minutes, or says `Missing GROQ_API_KEY`, `404` or `413` | See section 6 and [evals/README.md](evals/README.md): call `promptfoo` directly, pass `--env-file evals/.env -j 1`, and use a model your key serves. |
| A task went straight to `FAILED` | Open it on the task board: the reason is on its card and in the log at `ERROR` level (a box already reserved, a destination that doesn't resolve, a robot in an error state, or the gate). |
| A robot sits in `WAITING` or `ERROR` | `WAITING`: it yields to another robot or waits on a person (the robot panel shows the reason; [TRUST_LAYER.md](TRUST_LAYER.md#physical-waits)). `ERROR`: its battery hit zero or its controller raised; press **Reset** on the robot. |
| Everything is confused after editing code | `POST /api/state/reset`, or **Reset** in the dashboard, puts the floor back as its seed left it. |
