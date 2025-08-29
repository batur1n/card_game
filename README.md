# Multiplayer Card Game Setup

## Requirements

Create a `requirements.txt` file:

```
fastapi==0.104.1
uvicorn[standard]==0.24.0
websockets==12.0
python-multipart==0.0.6
```

## Project Structure

```
card_game/
├── main.py              # Backend FastAPI application
├── requirements.txt     # Python dependencies
├── static/
│   └── index.html      # Frontend HTML file
└── README.md           # This file
```

## Installation & Setup

1. **Install Python 3.8+** if not already installed

2. **Create virtual environment:**
   ```bash
   python -m venv card_game_env
   source card_game_env/bin/activate  # On Windows: card_game_env\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Create the static directory:**
   ```bash
   mkdir static
   ```

5. **Save the HTML file as `static/index.html`**

6. **Run the application:**
   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```

7. **Access the game:**
   Open your browser and go to `http://localhost:8000`

## Game Features Implemented

### ✅ Core Features
- **Multiplayer Support**: 2-6 players can join rooms via URL
- **Real-time Communication**: WebSocket-based real-time updates
- **Room Management**: Create and join game rooms
- **Player Ready System**: All players must be ready before game starts
- **Two-Phase Gameplay**: 
  - Phase 1: Card stacking with drag-and-drop
  - Phase 2: Card battle system (framework ready)

### ✅ UI Features
- **Drag & Drop**: Full drag-and-drop mechanics for card placement
- **Responsive Design**: Works on desktop and mobile
- **Real-time Updates**: Live game state synchronization
- **Visual Feedback**: Hover effects, drag indicators, and notifications
- **Player Sidebar**: Shows all players, their status, and turn indicators

### ✅ Game Logic (Partial)
- **Deck Management**: 36-card deck creation and shuffling
- **Card Dealing**: 2 hidden + 1 visible card per player
- **Turn System**: Proper turn rotation
- **Bad Card Counter**: Framework for rule violations
- **Trump Card System**: Last card becomes trump

## Game Rules Implementation Status

### Phase 1 (Stacking) - 🔄 Partially Implemented
- ✅ Standard 36-card deck shuffling
- ✅ Initial card dealing (2 hidden + 1 visible)
- ✅ Turn-based card drawing
- ✅ Basic card stacking mechanics
- 🔄 **Needs Completion**: 
  - Seniority validation (7 on 6, 8 on 7, etc.)
  - Bad card counter logic for rule violations
  - Automatic phase transition when deck is empty

### Donation Phase - 📋 Framework Ready
- 🔄 **Needs Implementation**:
  - Card distribution to players with bad cards
  - Donation interface and logic

### Phase 2 (Battle) - 📋 Framework Ready  
- 🔄 **Needs Implementation**:
  - Trump suit beating mechanics
  - Spades vs trump rules
  - Card battle resolution
  - Hidden card activation when hand is empty
  - Win/loss conditions

## Next Steps to Complete the Game

1. **Complete Phase 1 Logic**:
   ```python
   # Add to handle_place_card function
   def validate_card_placement(card, target_stack):
       if not target_stack:
           return True
       top_card = target_stack[-1]
       return card.rank == top_card.rank + 1
   ```

2. **Implement Bad Card Counter**:
   - Track rule violations
   - Implement 6-on-Ace penalty
   - Auto-increment counter for missed opportunities

3. **Add Donation Phase**:
   - UI for selecting cards to donate
   - Logic for distributing cards to penalized players

4. **Complete Phase 2**:
   - Card beating mechanics
   - Trump suit priority system
   - Battle pile management
   - Win condition detection

## Browser Compatibility

- ✅ Chrome/Edge (WebKit)
- ✅ Firefox
- ✅ Safari
- ✅ Mobile browsers

## Performance Notes

- WebSocket connections are efficient for real-time gameplay
- Cards are rendered as HTML/CSS (no heavy graphics)
- Game state is managed in-memory (scales to ~100 concurrent rooms)
- FastAPI provides excellent async performance

## Customization Options

The game is highly customizable:

1. **Change card deck size** in `create_deck()` method
2. **Modify player limits** by changing the `6` in `add_player()` 
3. **Adjust UI colors** in the CSS variables
4. **Add sound effects** by extending the JavaScript
5. **Implement different card rules** by modifying game logic

## Deployment Options

### Local Development
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Production (with Gunicorn)
```bash
pip install gunicorn
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### Docker Deployment
```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

This implementation provides a solid foundation for your multiplayer card game with modern web technologies, real-time communication, and an intuitive drag-and-drop interface. The remaining game logic can be implemented incrementally while players can already test the core multiplayer functionality.