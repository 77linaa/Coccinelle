# 🐞 Coccinelle — The Margin Forecast

### See the storm coming. Protect your portfolio.

**DurHack 2026 | SGX FX Challenge 1 — Protect Your Assets From a Trading Market Storm**

Coccinelle is an interactive FX risk-monitoring and portfolio-protection prototype. It combines historical market data, real-time risk detection, probabilistic margin forecasting, and simulated protective actions in one dashboard.

Our goal is simple: **give traders an early warning before market volatility turns into a margin crisis.**

---

## 🎯 The Problem

Foreign exchange markets can move rapidly during economic announcements, geopolitical events, and unexpected market shocks.

For leveraged traders, adverse price movements can reduce account equity and trigger margin calls, potentially forcing positions to close at the worst possible moment.

Traditional monitoring often shows traders what has already happened.

**Coccinelle helps them understand what is happening, what could happen next, and how different protective actions might affect their portfolio.**

---

## ✨ Key Features

### 🌪️ 1. Storm Monitor

- Analyses historical FX price movements and bid-ask spreads.
- Calculates a market risk score from 0–100.
- Classifies conditions into four levels: Green, Yellow, Amber, and Red.
- Detects changes in risk levels and generates alerts.
- Displays market conditions through an animated storm radar.
- Supports historical replay with playback and timeline controls.

### 📈 2. Margin Forecast

- Estimates the probability of breaching maintenance margin.
- Uses Monte Carlo simulation to model possible portfolio outcomes.
- Displays a forecast cone showing uncertainty across simulated paths.
- Helps traders understand potential downside exposure.

### 💼 3. Portfolio Exposure

- Tracks a simulated leveraged FX portfolio.
- Displays equity, unrealised profit/loss, margin utilisation, and leverage.
- Shows how market movements affect available margin.
- Highlights the relationship between portfolio exposure and market risk.

### 🛡️ 4. Protection Lab

- Allows users to explore simulated protective actions.
- Supports position reduction, position closure, and forward-hedge scenarios.
- Calculates estimated effects on portfolio risk.
- Applies deterministic Risk Officer checks before approving simulated actions.
- Provides a comparison between protected and unprotected outcomes.

### ⚖️ 5. Judge vs Coccinelle

An interactive paper-trading comparison between:

1. An unprotected portfolio.
2. A portfolio protected by a judge-selected action.
3. A retrospective Coccinelle strategy triggered at the first critical risk signal.

The dashboard compares outcomes such as equity, margin-buffer survival, and intervention timing.

**All protection actions are simulations. Coccinelle does not execute real trades.**

### 🤖 6. Explainable Risk Alerts

- Generates plain-English explanations of changing market conditions.
- Supports optional Anthropic-powered explanations.
- Uses deterministic template explanations when AI is disabled or unavailable.
- Separates numerical risk calculations from AI-generated explanations.

---

## 🏗️ Architecture

```text
          SGX Historical FX Dataset
                    |
                    v
          Data Loading & Cleaning
                    |
                    v
         Parquet Market Data Files
                    |
                    v
           Python Risk Engine
        - Price & spread analysis
        - Storm score calculation
        - Risk level detection
                    |
                    v
             FastAPI Backend
        - WebSocket market stream
        - Portfolio accounting
        - Monte Carlo forecasts
        - Protection simulation
        - Risk Officer
        - Optional AI explanations
                    |
             HTTP / WebSocket
                    |
                    v
          React + Vite Frontend
        - Storm Monitor
        - Margin Forecast
        - Exposure Dashboard
        - Protection Lab
        - Judge vs Coccinelle
```

---

## 🛠️ Technology Stack

| Component | Technologies |
|-----------|-------------|
| Frontend | React, Vite, Tailwind CSS, Recharts |
| Backend | Python, FastAPI, WebSockets |
| Market Analysis | Python, NumPy, pandas |
| Data Processing | pandas, PyArrow, Parquet |
| Forecasting | Monte Carlo simulation |
| AI Explanations | Anthropic API (optional) |
| Testing | pytest, Node.js test runner |
| Version Control | Git, GitHub |

---

## 📊 Market Data

Coccinelle uses historical FX pricing data supplied for the SGX challenge.

The dataset includes:

- USD/JPY
- EUR/USD
- USD/SGD
- Spot and forward market instruments

The data-processing pipeline converts the supplied archive into cleaned Parquet files.

The frontend also includes a small selection of real historical SGX quotes for all three spot pairs, allowing the dashboard to demonstrate market replay independently.

**Data limitations:**

- The integrated backend currently streams USD/JPY by default.
- EUR/USD and USD/SGD are available through the frontend's historical replay fixtures.
- The example portfolio and its positions are fictional.
- Historical market data is replayed; the application does not connect to a live trading exchange.

---

## 🚀 Getting Started

### Prerequisites

- Python
- Node.js and npm
- Git
- The original SGX challenge dataset archive (`Challenge1.zip`)

The following commands are intended for **Windows PowerShell**.

### 1. Clone the repository

```powershell
git clone https://github.com/77linaa/Coccinelle.git
cd Coccinelle
```

### 2. Set up the Python environment

From the repository root:

```powershell
python -m venv backend\.venv
```

Install backend dependencies:

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

### 3. Prepare the SGX dataset

Place the original `Challenge1.zip` somewhere accessible on your computer.

For example, if it is in Downloads:

```powershell
.\backend\.venv\Scripts\python.exe -m engine.data_loader --zip "$env:USERPROFILE\Downloads\Challenge1.zip"
```

This generates processed Parquet files in:

```text
data/processed/
```

The original dataset archive and generated Parquet files are not committed to Git.

### 4. Start the backend

In the repository root:

```powershell
$env:SENTINEL_SOURCE = "engine"
$env:SENTINEL_AI = "off"

.\backend\.venv\Scripts\python.exe -m uvicorn sentinel.api:app --app-dir backend --reload
```

The backend runs at:

**http://localhost:8000**

Verify it is running:

**http://localhost:8000/health**

Interactive API documentation:

**http://localhost:8000/docs**

> To enable Anthropic-powered explanations, configure an `ANTHROPIC_API_KEY` and set `SENTINEL_AI` to `on`. AI access is optional.

### 5. Start the frontend

Open a **second PowerShell terminal**:

```powershell
cd frontend
npm ci
npm run dev
```

Open:

**http://localhost:5173**

The dashboard should connect to the FastAPI backend automatically.

If the backend is unavailable, the frontend can run an interactive local demonstration with clearly labelled fallback data.

---

## 🎬 Recommended Demo Scenario

Coccinelle supports replaying a period of significant USD/JPY volatility from **30 July 2026**.

For the demonstration:

1. Start the backend with the processed SGX dataset.
2. Open the Coccinelle dashboard.
3. Select **USD/JPY**.
4. Use the **Before the Storm** shortcut to navigate to approximately **13:34 UTC**.
5. Start the historical replay.
6. Observe the risk engine identify worsening market conditions.
7. Examine the margin forecast and exposure dashboard.
8. Open **Protection Lab** to compare simulated protective actions.
9. Use **Judge vs Coccinelle** to compare paper-trading outcomes.

In the backend's historical USD/JPY risk analysis, the market transitions through:

| Time (UTC) | Risk Level | Storm Score |
|------------|------------|-------------|
| 13:36 | Yellow | 38.9 |
| 13:37 | Amber | 72.7 |
| 13:38 | Red | 88.2 |

These timestamps come from the backend risk engine. The frontend's standalone fixture mode uses illustrative risk scores, which may differ.

---

## 🧪 Testing

### Backend and Risk Engine

Run from the repository root:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend\tests engine\tests -q
```

### Frontend

```powershell
cd frontend
npm test
npm run build
```

The test suites cover market-data handling, risk calculations, forecasting, backend behaviour, API integration, and frontend functionality.

---

## 🔌 API Overview

The backend exposes a REST API and a WebSocket connection.

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Backend health and source status |
| `GET /forecast` | Latest margin forecast |
| `GET /replay` | Current replay state |
| `POST /replay/start` | Start or resume replay |
| `POST /replay/pause` | Pause replay |
| `POST /replay/speed` | Change replay speed |
| `POST /replay/jump` | Jump to a historical timestamp |
| `POST /simulate` | Preview a protective action |
| `POST /protect` | Apply a simulated judge action |
| `GET /protect` | Retrieve protection comparison |
| `POST /protect/reset` | Reset protection comparison |
| `WS /ws` | Stream market ticks, alerts, and forecasts |

WebSocket updates deliver market information to the dashboard without requiring repeated polling.

---

## 🔒 Safety and Limitations

Coccinelle is a **hackathon research and demonstration prototype**.

- It does not place real trades or handle real funds.
- Portfolio positions and margin settings are simulated.
- Forecast probabilities are model estimates, not guarantees.
- Protection comparisons are retrospective paper simulations.
- Forward-hedge examples do not represent executed SGX futures trades.
- No real-time exchange connectivity or production brokerage integration is provided.
- AI explanations are supplementary; deterministic calculations and risk checks handle financial logic.

Coccinelle is not intended to provide personalised financial advice or production-ready trading recommendations.

---

## 👥 Team

Built at **DurHack 2026** by:

| Team Member | Main Contribution |
|-------------|-------------------|
| **Meryem** | Historical market data, streaming engine, risk detection |
| **Helina** | FastAPI backend, portfolio forecasting, protection simulation |
| **Rayane El Mselmi** | Frontend development, dashboard design, visualisations, backend integration |

Developed for **SGX FX — Challenge 1: Protect Your Assets From a Trading Market Storm**.

---

### 🐞 Coccinelle

*Markets don't always give you time to react. Coccinelle helps you see the storm before it hits.*