# 1xBet Cloud Setup Guide

This guide will help you set up the 1xBet project using Supabase for PostgreSQL and cloud Redis services.

## Step 1: Set up Supabase Database

### 1. Create Supabase Account
1. Go to https://supabase.com/
2. Click "Start your project" and sign up
3. Create a new project

### 2. Get Database Connection Details
1. In your Supabase dashboard, go to **Settings** → **Database**
2. Copy the **Connection string** (URI format)
3. Note down your database password

### 3. Update Environment Variables
Replace the DATABASE_URL in your `.env` file with your Supabase connection string:

```bash
# Example Supabase connection string format:
DATABASE_URL=postgresql://postgres:[YOUR-PASSWORD]@db.[YOUR-PROJECT-REF].supabase.co:5432/postgres
```

## Step 2: Set up Cloud Redis

### Option A: Redis Cloud (Recommended)
1. Go to https://redis.com/try-free/
2. Create a free account
3. Create a new database
4. Copy the connection details

### Option B: Railway Redis
1. Go to https://railway.app/
2. Create an account
3. Create a new project
4. Add Redis service
5. Get connection details

### 3. Update Redis Environment Variables
Replace the REDIS_URL in your `.env` file:

```bash
# Example Redis Cloud connection string:
REDIS_URL=redis://default:[YOUR-PASSWORD]@[YOUR-HOST]:[PORT]

# Example Railway Redis connection string:
REDIS_URL=redis://default:[YOUR-PASSWORD]@[YOUR-HOST]:[PORT]
```

## Step 3: Complete Environment Setup

### 1. Update your `.env` file with these cloud settings:

```bash
# =============================================================================
# Database Configuration (Supabase)
# =============================================================================
DATABASE_URL=postgresql://postgres:[YOUR-SUPABASE-PASSWORD]@db.[YOUR-PROJECT-REF].supabase.co:5432/postgres

# =============================================================================
# Redis Configuration (Cloud Redis)
# =============================================================================
REDIS_URL=redis://default:[YOUR-REDIS-PASSWORD]@[YOUR-REDIS-HOST]:[PORT]

# =============================================================================
# JWT Configuration
# =============================================================================
JWT_SECRET=your-super-secret-jwt-key-change-in-production
JWT_EXPIRES_IN=7d
JWT_REFRESH_EXPIRES_IN=30d

# =============================================================================
# Server Configuration
# =============================================================================
NODE_ENV=development
PORT=3001
FRONTEND_URL=http://localhost:3000
ADMIN_URL=http://localhost:3002

# =============================================================================
# Security Configuration
# =============================================================================
BCRYPT_ROUNDS=12
RATE_LIMIT_WINDOW_MS=900000
RATE_LIMIT_MAX_REQUESTS=100
CORS_ORIGIN=http://localhost:3000

# =============================================================================
# Admin Configuration
# =============================================================================
ADMIN_EMAIL=admin@1xbet-clone.com
ADMIN_PASSWORD=admin123456
ADMIN_FIRST_NAME=Admin
ADMIN_LAST_NAME=User

# =============================================================================
# Development Configuration
# =============================================================================
DEBUG=false
LOG_REQUESTS=true
ENABLE_SWAGGER=true
SEED_DATA=true
```

## Step 4: Install Dependencies and Run

### 1. Install Backend Dependencies
```bash
cd backend
npm install
```

### 2. Install Frontend Dependencies
```bash
cd ../frontend
npm install
```

### 3. Set up Database Schema
```bash
cd ../backend
npx prisma generate
npx prisma migrate dev --name init
```

### 4. Seed Database
```bash
npm run seed
```

### 5. Start Backend Server
```bash
npm run dev
```

### 6. Start Frontend (in new terminal)
```bash
cd ../frontend
npm start
```

## Step 5: Verify Setup

### Check Backend Health
- Open: http://localhost:3001/health
- Should return: `{"status":"OK","timestamp":"...","uptime":...}`

### Check Frontend
- Open: http://localhost:3000
- Should show the 1xBet homepage

### Check Database Connection
```bash
cd backend
npx prisma studio
```
- This will open Prisma Studio in your browser
- You can view and manage your database data

## Troubleshooting

### Common Issues:

1. **Database Connection Error**
   - Verify your Supabase connection string
   - Check if your IP is whitelisted in Supabase
   - Ensure the database password is correct

2. **Redis Connection Error**
   - Verify your Redis connection string
   - Check if your Redis service is active
   - Ensure the password is correct

3. **Migration Errors**
   - Run: `npx prisma migrate reset`
   - Then: `npx prisma migrate dev`

4. **Port Already in Use**
   - Change PORT in .env file
   - Or kill the process using the port

## Next Steps

Once everything is running:

1. **Create Admin Account**: The seed script creates an admin user
2. **Explore Features**: Test betting, casino games, wallet functions
3. **Customize**: Modify odds, add sports, configure promotions
4. **Deploy**: When ready, deploy to production

## Support

If you encounter issues:
1. Check the console logs for error messages
2. Verify all environment variables are set correctly
3. Ensure all dependencies are installed
4. Check if cloud services are accessible 