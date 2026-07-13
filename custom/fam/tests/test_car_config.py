from fam import gate

def test_car_defaults_merged_into_old_config(tmp_path):
    live = tmp_path / "fam-config.json"
    live.write_text('{"target": "whatsapp:+1", "quiet_start": "21:30", '
                    '"quiet_end": "07:30"}', encoding="utf-8")
    cfg = gate.load_config(config_path=str(live))
    assert cfg["car_fuel_low_pct"] == 25
    assert cfg["car_warmup_daily_limit"] == 5
    assert cfg["car_cabin_suggest_enabled"] is True
    assert cfg["car_cabin_temp_low_c"] == 0
    assert cfg["car_cabin_temp_high_c"] == 30
    assert cfg["car_staleness_hours"] == 24
    assert cfg["car_poll_interval_min"] == 30
    assert cfg["car_fuel_hysteresis"] == 5
