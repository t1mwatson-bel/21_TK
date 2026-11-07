# 1xBet Clone - Sports Betting & Casino Platform

A fully-featured and scalable web application similar to 1xBet with sports betting, casino games, and comprehensive user management.

## 🎯 Features

### Core Functionality
- **Sports Betting**: Live and pre-match betting with real-time odds
- **Casino Games**: Integrated casino games with simulated gameplay
- **User Management**: Registration, KYC, 2FA, and user profiles
- **Wallet System**: Deposits, withdrawals, and transaction history
- **Promotions**: Welcome bonuses, daily rewards, and referral system
- **Admin Panel**: Complete management interface for operators

### Technical Stack
- **Frontend**: React.js with TypeScript, Tailwind CSS
- **Backend**: Node.js + Express, PostgreSQL, Redis
- **Real-time**: WebSocket for live updates
- **Infrastructure**: Docker, Nginx, CI/CD ready

## 🚀 Quick Start

### Prerequisites
- Node.js 18+
- Docker & Docker Compose
- PostgreSQL
- Redis

### Installation

1. **Clone and Setup**
```bash
git clone <repository-url>
cd 1xbet
```

2. **Environment Setup**
```bash
cp .env.example .env
# Edit .env with your configuration
```

3. **Start with Docker**
```bash
docker-compose up -d
```

4. **Manual Setup (Alternative)**
```bash
# Backend
cd backend
npm install
npm run dev

# Frontend
cd frontend
npm install
npm run dev
```

## 📁 Project Structure

```
1xbet/
├── frontend/                 # React.js frontend application
├── backend/                  # Node.js + Express backend
├── admin/                    # Admin panel interface
├── docker/                   # Docker configuration
├── docs/                     # Documentation
└── scripts/                  # Utility scripts
```

## 🔧 Development

### Backend API
- RESTful API with JWT authentication
- WebSocket for real-time updates
- Microservice architecture
- Rate limiting and security middleware

### Frontend Features
- Responsive design with dark/light mode
- Real-time odds updates
- Betting slip with calculations
- User dashboard and wallet management

### Admin Panel
- Match and odds management
- User management and KYC approval
- Financial reports and analytics
- System monitoring

## 🛡️ Security Features

- JWT-based authentication
- Input validation and sanitization
- Rate limiting and DDoS protection
- Admin route protection
- Fraud detection algorithms

## 📊 Database Schema

- Users, Wallets, Transactions
- Sports, Matches, Odds
- Bets, Betting History
- Casino Games, Game Results
- Promotions, Bonuses

## 🚀 Deployment

### Production Setup
```bash
# Build production images
docker-compose -f docker-compose.prod.yml build

# Deploy
docker-compose -f docker-compose.prod.yml up -d
```

### Environment Variables
See `.env.example` for all required environment variables.

## 📝 API Documentation

API documentation is available at `/api/docs` when running the backend.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## 📄 License

This project is for educational purposes only.

## 🆘 Support

For support and questions, please open an issue in the repository. 