# Calculator App

A React calculator app with FastAPI backend for calculations.

## Setup

### Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

### Frontend
```bash
cd frontend
npm install
npm start
```

## API Endpoints

- `POST /calculate` - Perform calculation

## Features

- Basic arithmetic operations (+, -, *, /)
- Error handling for invalid operations
- Real-time calculation results
- Clean, responsive UI