# tests

The engine's test suite: about 80 `unittest` files, about 1,000 tests.
No network and no model API calls: models are scripted or hostile fakes
from `backends.py`, and sidecar tests start the real `awino_sidecar.py` as a
subprocess on the echo or scripted provider.

## Run

```bash
cd prototype
./run_tests.sh                                  # sweeps stray temp dirs before and after
python3 -m unittest discover -s tests -t .      # plain run
python3 -m unittest tests.test_receipt -v       # one file
```

A full run takes a minute or two. Set `TMPDIR` to a roomy folder if `/tmp`
is small. Six tests in `test_aws_sigv4.py` skip unless `botocore` is
installed; they cross-check the SigV4 signatures against it.

## Helpers

- `common.py`: `make_loop()` builds a `Loop` in a temp home with a scripted
  backend and judge. `PROTO` is the absolute path of `prototype/`, so tests
  do not depend on the working directory.
- `temp_sweep.py`: removes stale `awino-sidecar-test-*` and
  `awino-home-test-*` temp dirs left by crashed runs. Used by
  `run_tests.sh`.

## Where to look

| Area | Files |
|---|---|
| Contract, validation, judges | `test_contract*.py`, `test_validation.py`, `test_header_gate.py`, `test_judge*.py`, `test_done_forgery.py` |
| Phases and the repair loop | `test_e2e_mission.py`, `test_repair_elevator.py`, `test_verify_gate.py`, `test_verify_recipe.py`, `test_floor_scan.py` |
| Stances, modes, skills | `test_stances.py`, `test_model_stance.py`, `test_modes*.py`, `test_skills.py`, `test_skill_security.py`, `test_layered_loading.py`, `test_osmani.py`, `test_rigor.py` |
| Approvals and autopilot | `test_approvals.py`, `test_approval_targets.py`, `test_autopilot_policy.py` |
| State, journal, recovery | `test_state.py`, `test_journal*.py`, `test_recovery.py`, `test_rollback.py`, `test_session_start.py`, `test_isolation.py` |
| Stories, receipts, lessons | `test_story*.py`, `test_receipt*.py`, `test_lessons*.py`, `test_sidecar_stories.py` |
| Tools | `test_sandbox.py`, `test_patch_tool.py`, `test_tool_prompt.py`, `test_delegated_apply.py`, `test_cancellation.py` |
| Providers and model I/O | `test_model_io.py`, `test_native_tools_auth.py`, `test_aws_sigv4.py`, `test_ollama_backend.py` |
| Sidecar | `test_sidecar*.py` |
| Setup and CLI | `test_bootstrap.py`, `test_auto_init.py`, `test_setup_autopilot.py`, `test_cli.py`, `test_packaging.py`, `test_windows_compat.py` |
| Integrations | `test_mcp.py`, `test_awino_gate.py`, `test_hooks.py` |

A new feature needs a test that tries to defeat it, not only one that shows
it working. See [AUTHORING_TEMPLATE.md](../../AUTHORING_TEMPLATE.md).
