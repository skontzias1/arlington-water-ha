"""Tests. Account numbers and IDs are made up."""

from datetime import date, timedelta
from unittest.mock import patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.arlington_water import const, coordinator, portal, rates
from custom_components.arlington_water.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

B = "https://myutilities.arlingtonva.us"
LOGIN = '<form action="/css/public/login" name="loginForm"><input type="hidden" name="_csrf" value="tok"><input name="username"></form>'
ACCOUNT = """<div><p><span>Account Type:</span> <span>Residential</span></p>
<p><span>Current Balance:</span><span>$0.00</span> <a>Current Bill</a></p>
<p><span>Bill Due Date:</span> <span> 11/20/2025</span></p><p><span>Last Payment Date:</span><span>11/01/2025</span></p>
<p><span>Last Payment:</span><span>$250.00</span></p><p><span>Past Due Amount:</span><span>$0.00</span></p>
<p>Water <a href="/css/utility/consumptionChart/111111/2">View Consumption</a></p></div>"""
TX = """<table id="accountTransTable2"><thead><tr><th>Date</th><th>Type</th><th>Due Date</th><th>Amount</th><th>Balance</th></tr></thead><tbody>
<tr><td>Nov 01, 2025 20:00:00</td><td>WF Bank Debit Payment</td><td></td><td>-250.00</td><td>0.00</td></tr>
<tr><td>Oct 20, 2025</td><td><a href="/x"> Bill Print Oct 20</a></td><td>Nov 20, 2025</td><td>250.00</td><td>250.00</td></tr>
<tr><td>Aug 25, 2025</td><td>Late Fee Penalty</td><td></td><td>5.00</td><td>5.00</td></tr>
<tr><td>Jul 20, 2025</td><td>Bill Print Jul 20</td><td>Aug 20, 2025</td><td>200.00</td><td>200.00</td></tr></tbody></table>"""
READS = """<table id="meterReadingTable"><thead><tr><th>Read Date</th><th>Bill Date</th><th>Days</th><th>Read Amount</th><th>Consumption ( 1,000 Gals )</th><th>Average Daily</th></tr></thead><tbody>
<tr><td>Oct 1, 2025</td><td>Oct 20, 2025</td><td>92</td><td>300</td><td>12</td><td>0.13</td></tr>
<tr><td>Jul 1, 2025</td><td>Jul 20, 2025</td><td>91</td><td>288</td><td>10</td><td>0.11</td></tr>
<tr><td>Apr 1, 2025</td><td>Apr 20, 2025</td><td>90</td><td>278</td><td>9</td><td>0.10</td></tr></tbody></table>"""


@pytest.fixture(autouse=True)
def _custom(recorder_mock, enable_custom_integrations):
    return


def site(mock, *, good=True, account=ACCOUNT, tx=TX, reads=READS, status=200):
    mock.clear_requests()
    mock.get(f"{B}/css/public/login/form", text=LOGIN, status=status)
    mock.post(
        f"{B}/css/public/login",
        status=302,
        headers={
            "Location": "/css/account/getAccount"
            if good
            else "/css/public/login/form?error"
        },
    )
    mock.get(f"{B}/css/public/login/form?error", text=LOGIN)
    mock.get(f"{B}/css/account/getAccount", text=account)
    mock.get(f"{B}/css/account/accountTransaction", text=tx)
    mock.get(f"{B}/css/utility/consumptionChart/111111/2", text=reads)


def entry_for(hass, **extra):
    e = MockConfigEntry(
        domain=const.DOMAIN,
        data={"username": "a@example.com", "password": "pw", **extra},
    )
    e.add_to_hass(hass)
    return e


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def poll(hass, entry):
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def state(hass, entry, key):
    eid = er.async_get(hass).async_get_entity_id(
        "sensor", const.DOMAIN, f"{entry.entry_id}_{key}"
    )
    return hass.states.get(eid).state


def posts(mock):
    return sum(1 for c in mock.mock_calls if c[0] == "POST")


async def test_happy_path(hass, aioclient_mock):
    site(aioclient_mock)
    e = entry_for(hass)
    await setup(hass, e)
    assert posts(aioclient_mock) == 0  # setup never touches the network
    await poll(hass, e)
    assert state(hass, e, "last_bill") == "250.0"
    assert state(hass, e, "last_bill_gallons") == "12000.0"
    assert state(hass, e, "meter_reading") == "300000.0"
    assert state(hass, e, "last_read") == "2025-10-01"
    assert state(hass, e, "due_date") == "2025-11-20"
    assert state(hass, e, "balance") == "0.0"
    assert state(hass, e, "last_payment") == "250.0"
    assert state(hass, e, "water_rate") == "unavailable"  # no meters chosen
    assert all(
        c[0] == "GET" or c[1].path == "/css/public/login"
        for c in aioclient_mock.mock_calls
    )
    await async_wait_recording_done(hass)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        dt_util.parse_datetime("2025-01-01T00:00:00+00:00"),
        None,
        {"arlington_water:consumption", "arlington_water:cost"},
        "hour",
        None,
        {"sum"},
    )
    assert stats["arlington_water:consumption"][-1]["sum"] == pytest.approx(22000)
    assert stats["arlington_water:cost"][-1]["sum"] == pytest.approx(450)
    assert len(stats["arlington_water:cost"]) == 92 + 91


async def test_network_waits_for_start(hass, aioclient_mock):
    site(aioclient_mock)
    hass.set_state(CoreState.not_running)
    e = entry_for(hass)
    await setup(hass, e)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5))
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 0
    await hass.async_start()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=10))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert posts(aioclient_mock) == 1


async def test_bad_password_tried_once_even_after_reload(hass, aioclient_mock):
    site(aioclient_mock, good=False)
    e = entry_for(hass)
    await setup(hass, e)
    for _ in range(3):
        await poll(hass, e)
    await hass.config_entries.async_reload(e.entry_id)
    await hass.async_block_till_done()
    await poll(hass, e)
    assert posts(aioclient_mock) == 1
    assert hass.config_entries.flow.async_progress_by_handler(const.DOMAIN)
    flow = hass.config_entries.flow.async_progress_by_handler(const.DOMAIN)[0]
    site(aioclient_mock)
    r = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"password": "new"}
    )
    assert r["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert e.data["blocked"] is False


@pytest.mark.parametrize(
    "kw",
    [
        {"status": 503},
        {"account": "<html>maintenance</html>"},
        {"tx": "<table id='accountTransTable2'><tr><th>New</th></tr></table>"},
        {"reads": READS.replace(">12<", ">abc<")},
        {"reads": READS.replace(">92<", ">9999<")},
        {"reads": READS.replace("Oct 1, 2025", "Oct 1, 2099")},
        {"account": LOGIN},
        {"tx": "x" * 2_100_000},
    ],
)
async def test_failures_keep_values(hass, aioclient_mock, kw, freezer):
    site(aioclient_mock)
    e = entry_for(hass)
    await setup(hass, e)
    await poll(hass, e)
    freezer.tick(timedelta(hours=25))
    site(aioclient_mock, **kw)
    await poll(hass, e)
    assert e.runtime_data.last_update_success is False
    assert e.state is ConfigEntryState.LOADED
    assert state(hass, e, "last_bill") == "250.0"
    n = posts(aioclient_mock)
    for _ in range(5):
        await poll(hass, e)  # rate limited
    assert posts(aioclient_mock) == n


@pytest.mark.parametrize(
    "exc",
    [aiohttp.ClientError, TimeoutError, RuntimeError, MemoryError, RecursionError],
)
async def test_exceptions_contained(hass, aioclient_mock, exc):
    e = entry_for(hass)
    await setup(hass, e)
    with patch.object(coordinator.Client, "fetch", side_effect=exc("x")):
        await poll(hass, e)
    assert e.state is ConfigEntryState.LOADED
    assert e.runtime_data.failures == 1


async def test_backoff_and_recovery(hass, aioclient_mock, freezer):
    site(aioclient_mock, status=503)
    e = entry_for(hass)
    await setup(hass, e)
    for _ in range(5):
        await poll(hass, e)
        freezer.tick(timedelta(hours=6, minutes=1))
    assert e.runtime_data.failures == 5
    site(aioclient_mock)
    await poll(hass, e)
    assert aioclient_mock.call_count == 0  # weekly now
    freezer.tick(timedelta(days=7))
    await poll(hass, e)
    assert e.runtime_data.failures == 0
    assert state(hass, e, "last_bill") == "250.0"


async def test_restart_loop_does_not_log_in_again(hass, aioclient_mock):
    site(aioclient_mock)
    e = entry_for(hass)
    await setup(hass, e)
    await poll(hass, e)
    for _ in range(5):
        await hass.config_entries.async_reload(e.entry_id)
        await hass.async_block_till_done()
        await poll(hass, e)
    assert posts(aioclient_mock) == 1
    assert state(hass, e, "last_bill") == "250.0"  # from cache


@pytest.mark.parametrize(
    "bad",
    [
        {"bills": "junk"},
        {"bills": [{"x": 1}]},
        {"summary": {"balance": 1}},
        {"failures": "q"},
    ],
)
async def test_damaged_cache(hass, aioclient_mock, hass_storage, bad):
    site(aioclient_mock)
    e = entry_for(hass)
    key = f"{const.DOMAIN}.{e.entry_id}"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": bad}
    await setup(hass, e)
    await poll(hass, e)
    assert e.state is ConfigEntryState.LOADED
    assert posts(aioclient_mock) == 0


async def test_redirect_off_host_refused(hass, aioclient_mock):
    site(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{B}/css/public/login/form", text=LOGIN)
    aioclient_mock.post(
        f"{B}/css/public/login",
        status=302,
        headers={"Location": "https://evil.example/x"},
    )
    e = entry_for(hass)
    await setup(hass, e)
    await poll(hass, e)
    assert e.runtime_data.last_error == "unexpected redirect"
    assert aioclient_mock.call_count == 2


async def test_statistics_failure_contained(hass, aioclient_mock):
    site(aioclient_mock)
    e = entry_for(hass)
    await setup(hass, e)
    with (
        patch.object(
            coordinator, "async_add_external_statistics", side_effect=ValueError
        ),
        patch.object(coordinator, "statistics_during_period", side_effect=ValueError),
    ):
        await poll(hass, e)
    assert state(hass, e, "last_bill") == "250.0"


async def test_flow_and_unload(hass, aioclient_mock):
    site(aioclient_mock, good=False)
    r = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": "user"}
    )
    data = {"username": "a@example.com", "password": "pw"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], data)
    assert r["errors"] == {"base": "invalid_auth"}
    site(aioclient_mock)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], data)
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    e = hass.config_entries.async_entries(const.DOMAIN)[0]
    assert await hass.config_entries.async_remove(e.entry_id)


def test_rates():
    old, new = rates.RATES[0], rates.RATES[1]
    assert rates.current(date(2026, 6, 30)) is old
    assert rates.current(date(2026, 7, 1)) is new
    assert rates.current(date(2030, 1, 1)) is new  # expired rates stay in use
    assert rates.current(date(2020, 1, 1)) is old
    assert rates.next_gallon(new, 5000, 8) == 0.01521  # tier 1 + sewer
    assert rates.next_gallon(new, 8500, 8) == 0.00473  # past the sewer cap
    assert rates.next_gallon(new, 9000, 8) == 0.00757
    assert rates.next_gallon(new, 9000, None) == 0.01805
    fixed = 17.51 + 13.98
    assert rates.estimate(new, 12000, 8, False) == pytest.approx(
        fixed + 9 * 4.73 + 3 * 7.57 + 8 * 10.48, abs=0.01
    )
    assert rates.estimate(new, 5000, 8, False) == pytest.approx(
        fixed + 5 * (4.73 + 10.48), abs=0.01
    )
    assert rates.estimate(new, 0, None, True) == pytest.approx(
        fixed + 439.27 / 4, abs=0.01
    )


async def test_options_and_estimate(hass, aioclient_mock):
    site(aioclient_mock)
    e = entry_for(hass)
    await setup(hass, e)
    await poll(hass, e)
    r = await hass.config_entries.options.async_init(e.entry_id)
    r = await hass.config_entries.options.async_configure(
        r["flow_id"],
        {
            const.CONF_METERS: ["sensor.one", "sensor.two"],
            const.CONF_WINTER_TG: 8,
            const.CONF_REFUSE: False,
        },
    )
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    fake = {
        "sensor.one": [{"change": 5000.0}, {"change": None}],
        "sensor.two": [{"change": 1000.0}],
    }
    with patch.object(coordinator, "statistics_during_period", return_value=fake):
        await poll(hass, e)
    assert state(hass, e, "quarter_gallons") == "6000.0"
    assert state(hass, e, "water_rate") not in ("unavailable", "unknown")
    assert state(hass, e, "quarter_estimate") not in ("unavailable", "unknown")
    # Non-residential accounts get bills but no residential pricing.
    e.runtime_data.data.summary = portal.Summary(0, 0, None, None, None, "Commercial")
    e.runtime_data.async_update_listeners()
    assert state(hass, e, "water_rate") == "unavailable"


async def test_upgrade_from_0_1_cache(hass, aioclient_mock, hass_storage):
    e = entry_for(hass)
    key = f"{const.DOMAIN}.{e.entry_id}"
    data = {
        "bills": [
            {
                "start": "2025-07-01",
                "end": "2025-10-01",
                "bill_date": "2025-10-20",
                "gallons": 12000.0,
                "reading_tg": 300.0,
                "amount": 250.0,
            }
        ],
        "summary": {
            "balance": 0.0,
            "past_due": 0.0,
            "due_date": "2025-11-20",
            "last_payment": 250.0,
            "last_payment_date": "2025-11-01",
        },
        "last_success": "2025-11-02T00:00:00+00:00",
        "failures": 0,
        "last_error": None,
        "next_portal": "2999-01-01T00:00:00+00:00",
    }
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": data}
    await setup(hass, e)
    assert state(hass, e, "last_bill") == "250.0"
    assert state(hass, e, "water_rate") == "unavailable"
    site(aioclient_mock, status=503)
    for _ in range(3):  # one try, even if it fails and HA keeps restarting
        await poll(hass, e)
        await hass.config_entries.async_reload(e.entry_id)
        await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1


async def test_setup_rejects_unsupported_account(hass, aioclient_mock):
    site(
        aioclient_mock,
        account=ACCOUNT + '<a href="/css/utility/consumptionChart/222222/2">x</a>',
    )
    r = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": "user"}
    )
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"username": "a@example.com", "password": "pw"}
    )
    assert r["errors"] == {"base": "unsupported"}


async def test_diagnostics_hold_nothing_private(hass, aioclient_mock):
    site(aioclient_mock)
    e = entry_for(hass)
    await setup(hass, e)
    await poll(hass, e)
    text = str(await async_get_config_entry_diagnostics(hass, e))
    for secret in ("example.com", "pw", "250", "300", "111111"):
        assert secret not in text


def test_merge_keeps_old_history():
    old = portal.Bill(date(2025, 1, 1), date(2025, 4, 1), date(2025, 5, 1), 1, 1, 1)
    new = portal.Bill(date(2026, 1, 1), date(2026, 4, 1), date(2026, 5, 1), 1, 1, 1)
    assert portal.merge([old], [new]) == [old, new]
