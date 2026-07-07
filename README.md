# campulse-backend

# CamPulse

> **The Pulse of Cambodia's Financial Market**

CamPulse is an all-in-one financial platform for tracking the **Cambodia Securities Exchange (CSX)**, **Gold Prices**, **Exchange Rates**, and **personal investment portfolios**.

The platform is available through:

* 🌐 Web Application
* 📱 Mobile Application
* 🤖 Telegram Bot

Our mission is to provide Cambodian investors with real-time market information, portfolio management, intelligent analytics, and investment insights in a single ecosystem.

---

# Features

## 📈 Market Tracking

Monitor Cambodia's financial markets in real time.

### Cambodia Stock Market (CSX)

* Live stock prices
* Company information
* Historical price charts
* Market overview
* Top gainers
* Top losers
* Trading volume
* Daily market summary

### Gold Prices

* Gold price tracking
* Historical trends
* Daily movement
* Price alerts

### Exchange Rates

* USD/KHR
* THB/KHR
* EUR/KHR
* CNY/KHR
* Other major currencies

---

# Portfolio Management

Track your investment portfolio with automatic profit/loss calculation.

## Lowest Price First Matching

CamPulse currently uses **Lowest Price First** matching when processing sell orders.

When selling shares:

* Lowest purchase prices are matched first.
* Remaining higher-cost lots stay in your portfolio.
* Realised profit/loss is calculated automatically.
* Matched buy orders are recorded for complete trade history.

---

# Telegram Commands

## Trading

```
/buy$ABC 7300 100
```

Buy 100 shares of **ABC** at **7,300 KHR**.

```
/sell$ABC 7400 100
```

Sell 100 shares and automatically calculate realised profit/loss.

---

## Portfolio

```
/portfolio
```

View your complete investment portfolio.

```
/position ABC
```

Display current holdings for a specific stock.

```
/stock ABC
```

View detailed stock allocation and matched orders.

---

## Market

```
/price$ABC
```

Latest stock price.

```
/show_all
```

Display all available market prices.

---

## Analytics

```
/top_orders
```

Top five most profitable trades.

```
/top_tickers
```

Top five most profitable stocks.

---

# Visual Dashboard

## Stock Details

The **/stock** command provides a graphical dashboard including:

* Buy order history
* Sell order history
* Matched transactions
* Remaining lots
* Realised profit/loss
* Position summary

---

## Position Overview

The **/position** command displays:

* Total shares purchased
* Total shares sold
* Remaining holdings
* Open lots
* Percentage sold

---

# Example

## Scenario

```
Buy 100 @ 7,000
Buy 100 @ 7,100
Buy 100 @ 7,200

Sell 150 @ 7,500
```

### Matching

```
100 @ 7,000
50 @ 7,100
```

### Profit

```
100 × (7,500 − 7,000)

+

50 × (7,500 − 7,100)

=

70,000 KHR
```

---

# Architecture

```
Web Application
        │
        │
Mobile Application
        │
        │
Telegram Bot
        │
        ▼
      REST API
        │
        ▼
 Spring Boot Backend
        │
 ├── Portfolio
 ├── CSX Market
 ├── Gold
 ├── Exchange Rate
 └── Notification
        │
        ▼
   PostgreSQL Database
```

---

# Technology Stack

## Backend

* Java 21
* Spring Boot
* Spring Security
* JWT Authentication
* PostgreSQL
* Flyway

## Frontend

* Angular
* Tailwind CSS

## Mobile

* Flutter

## Messaging

* Telegram Bot API

## Infrastructure

* Docker
* GitHub Actions

---

# Project Roadmap

## Version 1

* CSX price tracking
* Trading journal
* Portfolio management
* Telegram Bot

## Version 2

* Gold prices
* Exchange rates
* Price alerts
* Market dashboard

## Version 3

* AI market summaries
* Watchlists
* Technical indicators
* Investment analytics

## Version 4

* Financial news
* Portfolio performance
* Dividend tracking
* Economic calendar

## Version 5

* US stocks
* Cryptocurrency
* Commodities
* AI investment assistant

---

# Current Status

## Completed

* Buy/Sell transaction recording
* Lowest Price First matching
* Portfolio management
* Position visualisation
* Trade history
* Profit/Loss calculation
* Telegram Bot integration

## In Progress

* CSX live market integration
* Gold price service
* Exchange rate service
* Web dashboard
* Mobile application

---

# Vision

CamPulse aims to become Cambodia's leading investment platform by bringing together:

* Cambodia Stock Market
* Gold Prices
* Exchange Rates
* Investment Portfolio
* AI Insights
* Smart Alerts
* Financial Analytics

into one modern ecosystem accessible from the Web, Mobile, and Telegram.
