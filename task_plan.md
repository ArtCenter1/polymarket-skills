# Task Plan: Issue #2 - Web Frontend Security Enhancements and Proxy Configuration

## Phases

### Phase 0: Initialization & Git Workflow
- [x] Checkout `main` and create `feature/issue-2-frontend-security` branch
- [x] Create planning files (`task_plan.md`, `findings.md`, `progress.md`)

### Phase 1: Refactor Subprocess Calls (shell=True → list-based)
- [ ] Review all `subprocess.run` calls in `web_frontend.py`
- [ ] Refactor `run_command()` to accept list-based args with `shell=False`
- [ ] Update all callers to pass list-based args
- [ ] Ensure `sys.executable` is used for Python subprocess calls

### Phase 2: Proxy & SSL Settings Persistence
- [ ] Create `config.json` file for persisting proxy/SSL settings
- [ ] Implement `load_config()` and `save_config()` functions in `web_frontend.py`
- [ ] Add settings propagation to subprocess environment

### Phase 3: API Endpoints for Settings
- [ ] Add `GET /api/settings` endpoint
- [ ] Add `POST /api/settings` endpoint

### Phase 4: Settings UI in Dashboard
- [ ] Add Settings button to header
- [ ] Add Settings modal with fields for HTTP_PROXY, HTTPS_PROXY, POLYMARKET_SSL_VERIFY
- [ ] Wire up load/save with fetch API calls

### Phase 5: Testing & Verification
- [ ] Verify `web_frontend.py` compiles without errors
- [ ] Verify all subprocess calls propagate proxy/SSL env vars
- [ ] Test endpoint updates with dummy requests

### Phase 6: PR Creation & Clean Up
- [ ] Commit changes incrementally
- [ ] Push to remote
- [ ] Create PR with descriptive body
- [ ] Update/clean up planning files
