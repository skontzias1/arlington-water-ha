<p align="center">
  <img src="assets/banner.svg" alt="Arlington County, Virginia water bills in Home Assistant (unofficial)" width="100%">
</p>

<p align="center">
  <a href="https://hacs.xyz/docs/faq/custom_repositories/"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS custom repository"></a>
  <a href="https://github.com/skontzias1/arlington-water-ha/releases/latest"><img src="https://img.shields.io/github/v/release/skontzias1/arlington-water-ha.svg" alt="Latest release"></a>
  <a href="https://github.com/skontzias1/arlington-water-ha/actions/workflows/validate.yml"><img src="https://github.com/skontzias1/arlington-water-ha/actions/workflows/validate.yml/badge.svg?branch=main" alt="Validate"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.11%2B-18BCF2.svg?logo=homeassistant&logoColor=white" alt="Home Assistant 2026.11 or newer">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/skontzias1/arlington-water-ha.svg" alt="MIT license"></a>
</p>

# Arlington Water for Home Assistant

<p align="center">
  <a href="#installation">Installation</a> ·
  <a href="#sensors">Sensors</a> ·
  <a href="#energy-dashboard">Energy dashboard</a> ·
  <a href="#privacy-and-safety">Privacy</a> ·
  <a href="#troubleshooting">Troubleshooting</a>
</p>

> [!IMPORTANT]
> **Unofficial.** Not affiliated with or endorsed by Arlington County or the portal's vendor.
> It reads your account pages on `myutilities.arlingtonva.us` and may stop working if the site changes.

## Features

- **Automatic:** sign in once; it checks once a day
- **Bill history** in long-term statistics, spread evenly over each bill's days
- **Account status:** balance, due date, last payment, meter reading
- **Optional estimate** of the next bill and the price of the next gallon, from your own water meters
- **Read-only** and no extra Python packages

## Compatibility

| Account | Status |
|---|---|
| Residential, one water service | ✅ Tested on one account |
| Commercial or multi-family | ⚠️ Untested. No rate or estimate |
| Several water services under one login | ❌ Rejected at setup |
| Arlington, Texas and other utilities | ❌ Not supported |

## Installation

Register at [myutilities.arlingtonva.us](https://myutilities.arlingtonva.us/) first. Use a password you use nowhere else.

### HACS (recommended)

[![Open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=skontzias1&repository=arlington-water-ha&category=integration)

1. Use the button, or HACS → ⋮ → **Custom repositories** → `https://github.com/skontzias1/arlington-water-ha`, type **Integration**.
2. Install **Arlington Water**.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/arlington_water` into `/config/custom_components/`.
2. Restart Home Assistant.

## Configuration

[![Add the Arlington Water integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=arlington_water)

Or Settings → Devices & services → **Add integration** → **Arlington Water**. Data appears about a minute later.

Options, under **Configure**. They only affect the rate and the estimate:

| Option | Use |
|---|---|
| Water meters | Sensors that together cover the whole property |
| Winter sewer use | Thousand gallons per quarter, from your bill. Empty: sewer is estimated on all use |
| Trash and recycling | Include the refuse charge |

## Sensors

| Sensor | Notes |
|---|---|
| Balance, Past due, Bill due | |
| Last payment, Last payment date | |
| Last bill, Last bill date, Last bill gallons, Last bill days | |
| Last meter read, Meter reading | County reads, in 1,000-gallon steps |
| Gallons since last read | Needs meters |
| Estimated bill so far | Needs meters. Use so far plus the full quarter's fixed charges |
| Water rate | Needs meters. Price of the next gallon, including sewer up to your winter use |
| Last successful update | Diagnostic, with the last error |

## Energy dashboard

Use **one** of these, or the same water is counted twice:

- your own water meters, with **Water rate** as the price
- the `arlington_water:consumption` and `arlington_water:cost` statistics

The cost statistic is the whole bill, including sewer and refuse.

## Limits

- **Not live.** Residential meters are read about once a quarter.
- History starts with the portal's last 12 months of transactions, then accumulates.
- Residential [rates](https://www.arlingtonva.us/Government/Programs/Water-Utilities/Customer-Service/Rates) are built in and need a yearly update. `rates_expired` on the Water rate sensor shows when.
- The estimate is not a bill.

## Privacy and safety

- Connects only to `myutilities.arlingtonva.us`. After an error it waits at least 6 hours, and a week after 5 errors.
- Home Assistant's startup never waits for the portal.
- A rejected password is not retried until you enter a new one.
- Requests time out, redirects to other hosts are refused, and implausible data is never stored.
- Home Assistant stores the login unencrypted in its configuration, and backups include it.
- Diagnostics leave out the login, account, meters and bill amounts.
- You are responsible for following the county's [terms](https://www.arlingtonva.us/About-Arlington/Terms-Conditions).

## Troubleshooting

| Message | Try |
|---|---|
| Sign-in rejected | Sign in on the website with the same login |
| Could not reach the portal | Check the website, then try again later |
| Account's pages are not supported | See the Home Assistant log and open an issue |
| Water rate unavailable | Choose meters in the options. Residential accounts only |

[![Report a bug](https://img.shields.io/badge/Report_a_bug-d73a4a?style=for-the-badge&logo=github&logoColor=white)](https://github.com/skontzias1/arlington-water-ha/issues/new?template=bug.md)

Never post your login, account number or bills.

## Remove

Remove any Energy price and the integration, then delete the `arlington_water:` statistics under Settings → Tools → Statistics.

## License

[MIT](LICENSE)
