# 1xBet Clone - Deployment Guide

This guide provides comprehensive instructions for deploying the 1xBet clone application in both development and production environments.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Development Setup](#development-setup)
3. [Production Deployment](#production-deployment)
4. [Environment Configuration](#environment-configuration)
5. [Database Setup](#database-setup)
6. [SSL Configuration](#ssl-configuration)
7. [Monitoring Setup](#monitoring-setup)
8. [Troubleshooting](#troubleshooting)

## Prerequisites

### System Requirements

- **Operating System**: Linux (Ubuntu 20.04+ recommended), macOS, or Windows with WSL2
- **Docker**: Version 20.10+
- **Docker Compose**: Version 2.0+
- **Node.js**: Version 18+ (for local development)
- **Git**: Latest version
- **Memory**: Minimum 4GB RAM, 8GB+ recommended
- **Storage**: Minimum 20GB free space

### Required Accounts

- **Email Service**: SendGrid, Mailgun, or AWS SES
- **SMS Service**: Twilio or AWS SNS
- **Payment Gateway**: Stripe, PayPal, or similar
- **File Storage**: AWS S3, Google Cloud Storage, or similar
- **Domain**: For production deployment

## Development Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd 1xbet
```

### 2. Environment Configuration

```bash
# Copy environment template
cp env.example .env

# Edit environment variables
nano .env
```

### 3. Start Development Environment

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### 4. Database Setup

```bash
# Run database migrations
docker-compose exec backend npx prisma migrate dev

# Seed database with initial data
docker-compose exec backend npm run seed
```

### 5. Access the Application

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:3001
- **Admin Panel**: http://localhost:3002
- **Database**: localhost:5432
- **Redis**: localhost:6379

## Production Deployment

### 1. Server Preparation

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/download/v2.20.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Add user to docker group
sudo usermod -aG docker $USER
```

### 2. Application Deployment

```bash
# Clone repository
git clone <repository-url>
cd 1xbet

# Copy production environment
cp env.example .env.prod

# Edit production environment
nano .env.prod

# Start production services
docker-compose -f docker-compose.prod.yml up -d

# Check service status
docker-compose -f docker-compose.prod.yml ps
```

### 3. Database Migration

```bash
# Run production migrations
docker-compose -f docker-compose.prod.yml exec backend npx prisma migrate deploy

# Seed production data
docker-compose -f docker-compose.prod.yml exec backend npm run seed:prod
```

### 4. SSL Certificate Setup

```bash
# Install Certbot
sudo apt install certbot python3-certbot-nginx

# Generate SSL certificate
sudo certbot --nginx -d your-domain.com -d www.your-domain.com

# Auto-renewal
sudo crontab -e
# Add: 0 12 * * * /usr/bin/certbot renew --quiet
```

## Environment Configuration

### Required Environment Variables

#### Database
```bash
DATABASE_URL=postgresql://user:password@host:5432/database
POSTGRES_PASSWORD=secure-password
```

#### Security
```bash
JWT_SECRET=your-super-secret-jwt-key
BCRYPT_ROUNDS=12
```

#### External Services
```bash
EMAIL_SERVICE_API_KEY=your-email-service-key
SMS_SERVICE_API_KEY=your-sms-service-key
PAYMENT_GATEWAY_KEY=your-payment-gateway-key
```

#### URLs
```bash
FRONTEND_URL=https://your-domain.com
ADMIN_URL=https://admin.your-domain.com
CORS_ORIGIN=https://your-domain.com
```

### Environment File Structure

```bash
# Development
.env

# Production
.env.prod

# Staging
.env.staging
```

## Database Setup

### 1. PostgreSQL Configuration

```sql
-- Create database
CREATE DATABASE 1xbet;

-- Create user
CREATE USER 1xbet_user WITH PASSWORD 'secure_password';

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE 1xbet TO 1xbet_user;
```

### 2. Redis Configuration

```bash
# Redis configuration file
cat > redis.conf << EOF
requirepass your-redis-password
maxmemory 256mb
maxmemory-policy allkeys-lru
save 900 1
save 300 10
save 60 10000
EOF
```

### 3. Database Backup

```bash
# Create backup script
cat > backup.sh << 'EOF'
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups"
mkdir -p $BACKUP_DIR

# PostgreSQL backup
docker-compose exec -T postgres pg_dump -U 1xbet_user 1xbet > $BACKUP_DIR/postgres_$DATE.sql

# Redis backup
docker-compose exec redis redis-cli --rdb /data/dump.rdb
docker cp 1xbet_redis:/data/dump.rdb $BACKUP_DIR/redis_$DATE.rdb

# Compress backups
tar -czf $BACKUP_DIR/backup_$DATE.tar.gz $BACKUP_DIR/postgres_$DATE.sql $BACKUP_DIR/redis_$DATE.rdb

# Clean up old backups (keep last 7 days)
find $BACKUP_DIR -name "backup_*.tar.gz" -mtime +7 -delete
EOF

chmod +x backup.sh
```

## SSL Configuration

### 1. Nginx SSL Configuration

```nginx
# Add to nginx.conf
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;
    ssl_prefer_server_ciphers off;
    
    # HSTS
    add_header Strict-Transport-Security "max-age=63072000" always;
}
```

### 2. SSL Certificate Renewal

```bash
# Create renewal script
cat > renew-ssl.sh << 'EOF'
#!/bin/bash
certbot renew --quiet
docker-compose -f docker-compose.prod.yml restart nginx
EOF

chmod +x renew-ssl.sh

# Add to crontab
echo "0 12 * * * /path/to/renew-ssl.sh" | crontab -
```

## Monitoring Setup

### 1. Prometheus Configuration

```yaml
# monitoring/prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: '1xbet-backend'
    static_configs:
      - targets: ['backend:3001']
    metrics_path: '/metrics'

  - job_name: '1xbet-frontend'
    static_configs:
      - targets: ['frontend:3000']

  - job_name: 'postgres'
    static_configs:
      - targets: ['postgres:5432']

  - job_name: 'redis'
    static_configs:
      - targets: ['redis:6379']
```

### 2. Grafana Dashboards

```bash
# Import dashboards
curl -X POST \
  -H "Content-Type: application/json" \
  -d @dashboard-1xbet.json \
  http://admin:password@localhost:3003/api/dashboards/db
```

### 3. Alert Configuration

```yaml
# monitoring/alerts.yml
groups:
  - name: 1xbet-alerts
    rules:
      - alert: HighCPUUsage
        expr: cpu_usage > 80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: High CPU usage detected
```

## Troubleshooting

### Common Issues

#### 1. Database Connection Issues

```bash
# Check database status
docker-compose exec postgres psql -U 1xbet_user -d 1xbet -c "SELECT version();"

# Reset database
docker-compose down -v
docker-compose up -d postgres
docker-compose exec backend npx prisma migrate reset
```

#### 2. Redis Connection Issues

```bash
# Check Redis status
docker-compose exec redis redis-cli ping

# Reset Redis
docker-compose exec redis redis-cli FLUSHALL
```

#### 3. Frontend Build Issues

```bash
# Clear node modules and rebuild
docker-compose exec frontend rm -rf node_modules
docker-compose exec frontend npm install
docker-compose restart frontend
```

#### 4. Backend API Issues

```bash
# Check backend logs
docker-compose logs backend

# Restart backend
docker-compose restart backend

# Check API health
curl http://localhost:3001/health
```

### Performance Optimization

#### 1. Database Optimization

```sql
-- Add indexes for better performance
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_bets_user_id ON bets(user_id);
CREATE INDEX idx_transactions_wallet_id ON transactions(wallet_id);
```

#### 2. Redis Optimization

```bash
# Redis configuration for production
maxmemory 512mb
maxmemory-policy allkeys-lru
save 900 1
save 300 10
save 60 10000
```

#### 3. Nginx Optimization

```nginx
# Enable gzip compression
gzip on;
gzip_vary on;
gzip_min_length 1024;
gzip_proxied any;
gzip_comp_level 6;

# Enable caching
location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
}
```

### Security Checklist

- [ ] Change default passwords
- [ ] Enable SSL/TLS
- [ ] Configure firewall rules
- [ ] Set up rate limiting
- [ ] Enable CORS properly
- [ ] Configure JWT secrets
- [ ] Set up monitoring and alerts
- [ ] Regular security updates
- [ ] Database backups
- [ ] Log monitoring

### Backup Strategy

```bash
# Automated backup script
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups"

# Database backup
pg_dump -h localhost -U 1xbet_user 1xbet > $BACKUP_DIR/db_$DATE.sql

# File uploads backup
tar -czf $BACKUP_DIR/uploads_$DATE.tar.gz /uploads

# Configuration backup
tar -czf $BACKUP_DIR/config_$DATE.tar.gz .env*

# Upload to cloud storage
aws s3 cp $BACKUP_DIR/db_$DATE.sql s3://backups/
aws s3 cp $BACKUP_DIR/uploads_$DATE.tar.gz s3://backups/
aws s3 cp $BACKUP_DIR/config_$DATE.tar.gz s3://backups/
```

## Support

For additional support:

1. Check the [README.md](README.md) for basic setup instructions
2. Review the [API Documentation](docs/API.md) for endpoint details
3. Check [Issues](https://github.com/your-repo/issues) for known problems
4. Create a new issue for bugs or feature requests

## License

This project is for educational purposes only. Please ensure compliance with local gambling laws and regulations. 