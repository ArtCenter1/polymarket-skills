# Polymarket Trading Agent Web Frontend

## Overview
A beautiful, responsive web interface for monitoring and interacting with the Polymarket trading agent system. Provides real-time dashboard views of portfolio performance, market opportunities, and trading signals.

## Features

### Real-Time Dashboard
- Portfolio value and performance metrics
- Active positions and daily P&L
- Win rate and trading statistics
- Interactive charts for portfolio trends

### Market Monitoring
- Live market data from Polymarket Gamma API
- Volume, liquidity, and price information
- Market status (active, accepting orders, end dates)
- Search and filter capabilities

### Opportunity Detection
- Arbitrage opportunities (underpriced/overpriced markets)
- Momentum signals (volume surges, price trends)
- Mean-reversion setups
- News-driven events
- Edge percentage, position sizing, and confidence scores

### Trading Interface
- One-click trade execution (paper trading mode)
- Trade confirmation dialogs
- Risk management visualization
- Trade history tracking

### System Health
- Portfolio health checks
- Risk limit monitoring
- Drawdown tracking
- System status indicators

## Technology Stack

- **Backend**: Python Flask API server
- **Frontend**: HTML5, Tailwind CSS, Chart.js
- **Data Sources**: Polymarket Gamma & CLOB APIs
- **Real-time Updates**: Background data fetching (30-second intervals)
- **Deployment**: Local development server

## Installation & Usage

### Prerequisites
1. Python 3.8+
2. Git
  
### Setup
```bash
# Clone repository
git clone https://github.com/ArtCenter1/polymarket-skills.git
cd polymarket-skills

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.\.venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt
```

### Running the Application
```bash
# Start the web frontend
python web_frontend.py

# Access the dashboard at:
# http://localhost:5000
```

### API Endpoints
- `GET /` - Main dashboard
- `GET /api/portfolio` - Portfolio data
- `GET /api/markets` - Active market data
- `GET /api/opportunities` - Trading opportunities
- `GET /api/monitor` - Real-time price monitoring
- `GET /api/status` - System status
- `POST /api/execute_trade` - Execute trade recommendation
- `GET /api/health_check` - Portfolio health check

## Data Flow
1. **Background Updates**: Every 30 seconds, the system fetches:
   - Portfolio status from paper trading engine
   - Active markets from Gamma API (volume > $10K filter)
   - Trading opportunities from edge detection scripts
   - Real-time prices for monitoring

2. **Frontend Updates**: Dashboard components refresh automatically:
   - Portfolio overview: Every 30 seconds
   - Market listings: Every 30 seconds
   - Opportunities list: Every 30 seconds
   - System status: Every 30 seconds

3. **User Interactions**:
   - Trade execution: Sends recommendation to paper trading engine
   - Refresh button: Manually triggers data updates
   - Market/opportunity details: Placeholder for future enhancement

## Screenshots & UI Components

### Dashboard Overview
- **Header**: System title, last update timestamp, manual refresh button
- **Status Cards**: Portfolio value, active positions, daily P&L, win rate
- **Charts Section**: Portfolio performance line chart, opportunities distribution doughnut chart
- **Markets Panel**: List of active markets with volume, prices, and action buttons
- **Opportunities Panel**: Detected trading edges with type, size, confidence, and execution buttons

### Styling & Responsiveness
- Built with Tailwind CSS for modern, responsive design
- Mobile-first layout that adapts to different screen sizes
- Color-coded indicators (green for profit, red for loss, blue for info)
- Hover effects and smooth transitions
- Loading states and error handling

## Customization

### Adjust Update Frequency
Modify the `background_updater()` function in `web_frontend.py`:
```python
time.sleep(30)  # Change 30 to desired seconds
```

### Change Default Market Filters
Edit the scan parameters in `update_markets()`:
```bash
markets_cmd = f"{sys.executable} polymarket-scanner/scripts/scan_markets.py --limit 20 --min-volume 10000"
```

### Customize Chart Data
Update the `initCharts()` function in the dashboard.html template:
- Portfolio chart: Modify datasets array
- Opportunities chart: Adjust labels and data arrays

## Security Notes
- **Paper Trading Only**: Web interface currently executes trades in paper mode only
- **No Wallet Exposure**: Private keys and wallet information are never exposed through the web interface
- **Read-Only Market Data**: All Gamma API calls are read-only and require no authentication
- **Local Execution**: Runs entirely on your machine - no data leaves your system

## Future Enhancements
- [ ] Live trading mode with proper authentication
- [ ] Advanced charting with technical indicators
- [ ] Strategy backtesting visualizer
- [ ] Alert notification system (email/webhook)
- [ ] Multi-timeframe analysis views
- [ ] Export/import strategy configurations
- [ ] User authentication and multiple portfolio support
- [ ] Dark/light theme toggle
- [ ] Mobile app version (React Native)

## Troubleshooting

### Common Issues
1. **"Import flask could not be resolved"**
   - Solution: Ensure Flask is installed: `pip install flask`

2. **Port already in use**
   - Solution: Change port in `app.run()` or kill existing process:
     ```bash
     # Find and kill process on port 5000
     lsof -ti:5000 | xargs kill -9  # Linux/Mac
     netstat -ano | findstr :5000   # Windows
     ```

3. **API connection errors**
   - Solution: Check internet connectivity and Polymarket API status
   - Verify `py-clob-client` is properly installed

4. **Template not found errors**
   - Solution: Ensure `templates/` directory exists and contains `dashboard.html`

### Logs and Debugging
- Run with debug mode: `python web_frontend.py` (shows Flask startup info)
- Check browser developer tools for frontend issues
- Monitor terminal output for backend errors
- Verify data flow by testing individual API endpoints:
  ```bash
  curl http://localhost:5000/api/portfolio
  curl http://localhost:5000/api/markets
  ```

## License
MIT License - feel free to modify and extend for your trading needs.

## Disclaimer
This web frontend is for use with the Polymarket paper trading system only. 
Always test strategies extensively in paper mode before considering live trading.
Prediction market trading involves risk of loss. Past performance does not guarantee future results.