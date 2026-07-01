#!/usr/bin/env python3
"""
Test config persistence for proxy/SSL settings.

Verifies that load_config/save_config round-trip correctly,
handles missing files, partial updates, and type coercion.
"""
import json
import os
import sys
import tempfile
import pathlib

# Add repo root to path
REPO = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

# Import from web_frontend after path setup
sys.path.insert(0, str(REPO))

# We'll test the logic directly by importing the functions
from web_frontend import load_config, save_config, DEFAULT_CONFIG, CONFIG_PATH

TEST_COUNT = 0
PASS_COUNT = 0


def test(name, condition, detail=""):
    global TEST_COUNT, PASS_COUNT
    TEST_COUNT += 1
    if condition:
        PASS_COUNT += 1
        print(f"  PASS: {name}")
    else:
        print(f"  FAIL: {name} -- {detail}")


def run_tests():
    global TEST_COUNT, PASS_COUNT

    print("=" * 60)
    print("Config Persistence Tests")
    print("=" * 60)

    # Store original config path
    original_config = CONFIG_PATH
    if original_config.exists():
        original_content = original_config.read_text()
    else:
        original_content = None

    try:
        # ---- Test 1: Default config when no file exists ----
        print("\n[Test 1: Default config when no file exists]")
        # Use a non-existent path
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_path = pathlib.Path(tmpdir) / "config.json"
            # Temporarily replace CONFIG_PATH
            import web_frontend
            old_path = web_frontend.CONFIG_PATH
            web_frontend.CONFIG_PATH = fake_path

            cfg = web_frontend.load_config()
            test("Returns dict", isinstance(cfg, dict))
            test("HTTP_PROXY default empty", cfg.get("HTTP_PROXY") == "")
            test("HTTPS_PROXY default empty", cfg.get("HTTPS_PROXY") == "")
            test("POLYMARKET_SSL_VERIFY default True", cfg.get("POLYMARKET_SSL_VERIFY") is True)

            # ---- Test 2: Save and reload ----
            print("\n[Test 2: Save and reload config]")
            web_frontend.save_config({
                "HTTP_PROXY": "http://127.0.0.1:7890",
                "HTTPS_PROXY": "http://127.0.0.1:7890",
                "POLYMARKET_SSL_VERIFY": False,
            })
            reloaded = web_frontend.load_config()
            test("HTTP_PROXY saved", reloaded.get("HTTP_PROXY") == "http://127.0.0.1:7890")
            test("HTTPS_PROXY saved", reloaded.get("HTTPS_PROXY") == "http://127.0.0.1:7890")
            test("SSL_VERIFY saved as False", reloaded.get("POLYMARKET_SSL_VERIFY") is False)

            # ---- Test 3: save_config fills missing keys with defaults ----
            # NOTE: Partial update merge happens in the API endpoint (POST /api/settings),
            # not in save_config() itself. save_config() is a low-level writer that
            # fills missing keys with defaults.
            print("\n[Test 3: save_config fills missing keys with defaults]")
            web_frontend.save_config({
                "HTTP_PROXY": "http://new-proxy:8080",
            })
            direct_save = web_frontend.load_config()
            test("HTTP_PROXY updated", direct_save.get("HTTP_PROXY") == "http://new-proxy:8080")
            test("HTTPS_PROXY defaulted to empty", direct_save.get("HTTPS_PROXY") == "")
            test("SSL_VERIFY defaulted to True", direct_save.get("POLYMARKET_SSL_VERIFY") is True)

            # ---- Test 3b: API-level merge preserves existing keys ----
            print("\n[Test 3b: API-level merge (simulated endpoint logic)]")
            # Simulate what POST /api/settings does
            current = web_frontend.load_config()
            # current has HTTP_PROXY="http://new-proxy:8080", HTTPS_PROXY="", SSL_VERIFY=True
            partial = {"HTTPS_PROXY": "https://special-proxy:8443"}
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "POLYMARKET_SSL_VERIFY"):
                if key in partial:
                    current[key] = partial[key]
            web_frontend.save_config(current)
            merged = web_frontend.load_config()
            test("HTTP_PROXY preserved from earlier", merged.get("HTTP_PROXY") == "http://new-proxy:8080")
            test("HTTPS_PROXY updated by partial", merged.get("HTTPS_PROXY") == "https://special-proxy:8443")
            test("SSL_VERIFY preserved from earlier", merged.get("POLYMARKET_SSL_VERIFY") is True)

            # ---- Test 4: Load from corrupt JSON returns defaults ----
            print("\n[Test 4: Corrupt JSON returns defaults]")
            fake_path.write_text("{bad json}", encoding="utf-8")
            corrupt = web_frontend.load_config()
            test("Returns dict on corrupt JSON", isinstance(corrupt, dict))
            test("HTTP_PROXY is default after corrupt", corrupt.get("HTTP_PROXY") == "")
            test("SSL_VERIFY default True after corrupt", corrupt.get("POLYMARKET_SSL_VERIFY") is True)

            # ---- Test 5: String bool coercion ----
            print("\n[Test 5: SSL_VERIFY string coercion]")
            fake_path.write_text(json.dumps({"POLYMARKET_SSL_VERIFY": "false"}), encoding="utf-8")
            coerced = web_frontend.load_config()
            test('SSL_VERIFY "false" string coerced to False', coerced.get("POLYMARKET_SSL_VERIFY") is False)

            fake_path.write_text(json.dumps({"POLYMARKET_SSL_VERIFY": "true"}), encoding="utf-8")
            coerced2 = web_frontend.load_config()
            test('SSL_VERIFY "true" string coerced to True', coerced2.get("POLYMARKET_SSL_VERIFY") is True)

            # Restore original path
            web_frontend.CONFIG_PATH = old_path

        # ---- Test 6: build_subprocess_env ----
        print("\n[Test 6: build_subprocess_env]")
        from web_frontend import build_subprocess_env
        env = build_subprocess_env()
        test("Returns a dict", isinstance(env, dict))
        test("Has POLYMARKET_SSL_VERIFY key", "POLYMARKET_SSL_VERIFY" in env)
        test("SSL_VERIFY is string", isinstance(env["POLYMARKET_SSL_VERIFY"], str))

        # ---- Test 7: run_command with list-based args ----
        print("\n[Test 7: run_command list-based usage]")
        from web_frontend import run_command, python_script_args
        args = python_script_args("_check_deps.py")
        test("Args is a list", isinstance(args, list))
        test("First arg is sys.executable", args[0] == sys.executable)
        test("Script path is in args", args[1].endswith("_check_deps.py"))

        # ---- Summary ----
        print("\n" + "=" * 60)
        print(f"Results: {PASS_COUNT}/{TEST_COUNT} passed")
        print("=" * 60)

    finally:
        # Restore original config if it existed
        if original_content is not None:
            original_config.write_text(original_content, encoding="utf-8")


if __name__ == "__main__":
    run_tests()
    sys.exit(0 if PASS_COUNT == TEST_COUNT else 1)
