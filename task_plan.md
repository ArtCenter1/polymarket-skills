# Task Plan: Issue #3 — Windows Compatibility Path Adjustments

## Phases

### Phase 0: Initialization ✅
- [x] Checkout `main` and create `feature/issue-3-windows-compat`
- [x] Initialize planning files (task_plan.md, findings.md, progress.md)

### Phase 1: Documentation Updates
- [ ] Update README.md — venv activation, setup commands for Windows + POSIX
- [ ] Update WEB_FRONTEND_README.md — verify Windows guidance already present, add any missing
- [ ] Update CLAUDE.md — Section 3 (Daily Workflow) and Section 4 (Trading Modes) venv commands

### Phase 2: Code Path Auditing
- [ ] Scan polymarket-scanner/ scripts for hardcoded paths
- [ ] Scan polymarket-analyzer/ scripts for hardcoded paths
- [ ] Scan polymarket-paper-trader/ scripts for hardcoded paths
- [ ] Scan polymarket-strategy-advisor/ scripts for hardcoded paths
- [ ] Scan polymarket-monitor/ scripts for hardcoded paths
- [ ] Scan polymarket-live-executor/ scripts for hardcoded paths
- [ ] Scan polymarket_common/ for shared path utilities

### Phase 3: Testing & Verification
- [ ] Verify all markdown files render cleanly
- [ ] Check all changed Python scripts compile without syntax errors

### Phase 4: Git Workflow & PR
- [ ] Commit changes incrementally
- [ ] Push branch to remote
- [ ] Create PR referencing "Closes #3"

## Status Key
- ✅ Complete
- ⬜ Not started
- 🔄 In progress
