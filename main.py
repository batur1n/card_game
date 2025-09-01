# main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import json
import uuid
import asyncio
from typing import Dict, List, Optional
from enum import Enum
import random

app = FastAPI()

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

class GamePhase(Enum):
    WAITING = "waiting"
    PHASE_ONE = "phase_one"
    DONATION = "donation"
    PHASE_TWO = "phase_two"
    FINISHED = "finished"

class Card:
    def __init__(self, suit: str, rank: int):
        self.suit = suit  # 'hearts', 'diamonds', 'clubs', 'spades'
        self.rank = rank  # 6-14 (6-10, J=11, Q=12, K=13, A=14)
        
    def __dict__(self):
        return {"suit": self.suit, "rank": self.rank}
    
    def to_dict(self):
        return {"suit": self.suit, "rank": self.rank}

class Player:
    def __init__(self, username: str, websocket: WebSocket):
        self.id = str(uuid.uuid4())
        self.username = username
        self.websocket = websocket
        self.ready = False
        self.hand = []
        self.visible_stack = []
        self.hidden_cards = []
        self.bad_card_counter = 0
        self.is_out = False
        self.has_drawn_this_turn = False
        self.cards_played_this_turn = 0

class GameRoom:
    def __init__(self, room_id: str):
        self.id = room_id
        self.players: List[Player] = []
        self.phase = GamePhase.WAITING
        self.deck = []
        self.current_player_index = 0
        self.trump_suit = None
        self.last_card_player = None
        self.battle_pile = []
        self.last_drawn_card = None
        self.losers_from_previous_game = []  # Track losers for turn order
        
    def add_player(self, player: Player):
        if len(self.players) < 6:
            self.players.append(player)
            return True
        return False
    
    def remove_player(self, player_id: str):
        self.players = [p for p in self.players if p.id != player_id]
    
    def get_player_by_id(self, player_id: str) -> Optional[Player]:
        return next((p for p in self.players if p.id == player_id), None)
    
    def create_deck(self):
        """Create standard 36 card deck (6-A in all suits)"""
        suits = ['hearts', 'diamonds', 'clubs', 'spades']
        self.deck = []
        for suit in suits:
            for rank in range(6, 15):  # 6-14 (A=14)
                self.deck.append(Card(suit, rank))
        random.shuffle(self.deck)
    
    def deal_initial_cards(self):
        """Deal 2 hidden + 1 visible card to each player"""
        for player in self.players:
            # 2 hidden cards
            player.hidden_cards = [self.deck.pop(), self.deck.pop()]
            # 1 visible card
            player.visible_stack = [self.deck.pop()]
    
    def can_stack_card(self, card: Card, target_stack: List[Card]) -> bool:
        """Check if card can be stacked on target (rank + 1 or 6 on Ace)"""
        if not target_stack:
            return True
        
        top_card = target_stack[-1]
        # Normal stacking rule: card rank = top card rank + 1
        if card.rank == top_card.rank + 1:
            return True
        
        # Special rule: 6 on Ace
        if card.rank == 6 and top_card.rank == 14:
            return True
        
        return False
    
    def get_valid_moves_for_player(self, player: Player) -> List[dict]:
        """Get all valid moves a player can make with their current hand"""
        valid_moves = []
        
        if not player.hand:
            return valid_moves
        
        # Check if player can place cards from their stack to others
        if len(player.visible_stack) > 1:  # Has more than just the base card
            top_card = player.visible_stack[-1]
            for target_player in self.players:
                if target_player.id != player.id:
                    if self.can_stack_card(top_card, target_player.visible_stack):
                        valid_moves.append({
                            'type': 'give_from_stack',
                            'card': top_card.to_dict(),
                            'target_player_id': target_player.id
                        })
        
        # Check if drawn card can be placed on other players
        if player.hand:
            for card in player.hand:
                for target_player in self.players:
                    if self.can_stack_card(card, target_player.visible_stack):
                        valid_moves.append({
                            'type': 'place_drawn_card',
                            'card': card.to_dict(),
                            'target_player_id': target_player.id
                        })
        
        return valid_moves
    
    def determine_first_player(self):
        """Determine who goes first based on previous game losers or random"""
        if self.losers_from_previous_game:
            # Find the first loser who is still in the game
            for loser_id in self.losers_from_previous_game:
                for i, player in enumerate(self.players):
                    if player.id == loser_id:
                        self.current_player_index = i
                        return
        
        # If no previous losers or they're not in game, random selection
        self.current_player_index = random.randint(0, len(self.players) - 1)
    
    def all_players_ready(self) -> bool:
        return len(self.players) >= 2 and all(p.ready for p in self.players)

# Global game state
rooms: Dict[str, GameRoom] = {}
connections: Dict[str, WebSocket] = {}

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
    
    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
    
    async def send_personal_message(self, message: str, client_id: str):
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_text(message)
    
    async def broadcast_to_room(self, message: str, room_id: str):
        if room_id in rooms:
            for player in rooms[room_id].players:
                if player.id in self.active_connections:
                    try:
                        await self.active_connections[player.id].send_text(message)
                    except:
                        pass

manager = ConnectionManager()

@app.get("/")
async def read_index():
    return FileResponse('static/index.html')

@app.get("/api/rooms")
async def get_rooms():
    room_list = []
    for room_id, room in rooms.items():
        if room.phase == GamePhase.WAITING:
            room_list.append({
                "id": room_id,
                "players": len(room.players),
                "max_players": 6
            })
    return {"rooms": room_list}

@app.post("/api/rooms")
async def create_room():
    room_id = str(uuid.uuid4())[:8]
    rooms[room_id] = GameRoom(room_id)
    return {"room_id": room_id}

@app.websocket("/ws/{room_id}/{username}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, username: str):
    player = Player(username, websocket)
    
    # Create room if it doesn't exist
    if room_id not in rooms:
        rooms[room_id] = GameRoom(room_id)
    
    room = rooms[room_id]
    
    if not room.add_player(player):
        await websocket.close(code=1000, reason="Room full")
        return
    
    await manager.connect(websocket, player.id)
    
    # Send initial game state
    await send_game_state(room)
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            await handle_message(room, player, message)
            
    except WebSocketDisconnect:
        room.remove_player(player.id)
        manager.disconnect(player.id)
        if room.players:
            await send_game_state(room)
        elif room_id in rooms and not room.players:
            del rooms[room_id]

async def handle_message(room: GameRoom, player: Player, message: dict):
    action = message.get("action")
    
    if action == "ready":
        player.ready = True
        if room.all_players_ready():
            await start_game(room)
        else:
            await send_game_state(room)
    
    elif action == "draw_card" and room.phase == GamePhase.PHASE_ONE:
        result = await handle_draw_card(room, player)
        await send_game_state(room)
    
    elif action == "place_card" and room.phase == GamePhase.PHASE_ONE:
        await handle_place_card(room, player, message)
    
    elif action == "give_from_stack" and room.phase == GamePhase.PHASE_ONE:
        await handle_give_from_stack(room, player, message)
    
    elif action == "beat_card" and room.phase == GamePhase.PHASE_TWO:
        await handle_beat_card(room, player, message)
    
    elif action == "take_pile" and room.phase == GamePhase.PHASE_TWO:
        await handle_take_pile(room, player)
    
    elif action == "end_turn" and room.phase == GamePhase.PHASE_ONE:
        # Remove manual end turn - turns should end automatically when placing on own stack
        # Only allow if player hasn't drawn yet and must draw
        player_obj = room.get_player_by_id(player.id)
        if (room.players[room.current_player_index].id == player.id and 
            not player_obj.hand and not room.deck):
            await end_player_turn(room, player)
    
    elif action == "donate_cards" and room.phase == GamePhase.DONATION:
        await handle_donate_cards(room, player, message)

async def start_game(room: GameRoom):
    room.phase = GamePhase.PHASE_ONE
    room.create_deck()
    room.deal_initial_cards()
    room.determine_first_player()
    
    # Reset player states
    for player in room.players:
        player.has_drawn_this_turn = False
        player.cards_played_this_turn = 0
        player.hand = []  # Players start with empty hands, must draw first
    
    await send_game_state(room)

async def handle_draw_card(room: GameRoom, player: Player):
    """Handle drawing a card from deck"""
    if room.players[room.current_player_index].id != player.id:
        return {"error": "Not your turn"}
    
    if not room.deck:
        return {"error": "Deck is empty"}
    
    if player.hand:  # Player already has a card in hand
        return {"error": "You already have a card in hand"}
    
    # Check if player should give from stack first (if they have more than 1 card in stack)
    if len(player.visible_stack) > 1:
        # Check if they have valid moves from their stack
        top_card = player.visible_stack[-1]
        has_valid_stack_move = False
        
        for target_player in room.players:
            if target_player.id != player.id:
                if room.can_stack_card(top_card, target_player.visible_stack):
                    has_valid_stack_move = True
                    break
        
        if has_valid_stack_move:
            # Player missed giving from stack, increment bad card counter
            player.bad_card_counter += 1
            await manager.send_personal_message(
                json.dumps({"type": "error", "message": "You should give from your stack first! Bad card counter increased."}), 
                player.id
            )
    
    # Draw card from deck
    drawn_card = room.deck.pop()
    player.hand = [drawn_card]  # Player can only have one card in hand
    room.last_drawn_card = drawn_card
    
    # Check if this was the last card - determine trump
    if not room.deck:
        if drawn_card.suit == 'spades':
            room.trump_suit = None  # No trump if last card is spade
        else:
            room.trump_suit = drawn_card.suit
        room.last_card_player = player
    
    return {"success": True, "drawn_card": drawn_card.to_dict()}

async def handle_place_card(room: GameRoom, player: Player, message: dict):
    """Handle placing a card from hand to a player's stack"""
    if room.players[room.current_player_index].id != player.id:
        return
    
    card_data = message.get("card")
    target_player_id = message.get("target_player_id")
    
    if not card_data or not target_player_id:
        return
    
    # Find the card in player's hand
    card_to_place = None
    for hand_card in player.hand:
        if hand_card.suit == card_data["suit"] and hand_card.rank == card_data["rank"]:
            card_to_place = hand_card
            break
    
    if not card_to_place:
        return
    
    # Find target player
    target_player = room.get_player_by_id(target_player_id)
    if not target_player:
        return
    
    # Check if placing on own stack or another player's stack
    placing_on_own_stack = (target_player_id == player.id)
    
    if not placing_on_own_stack:
        # Placing on another player's stack - check seniority rule
        if not room.can_stack_card(card_to_place, target_player.visible_stack):
            # Invalid move - increment bad card counter
            player.bad_card_counter += 1
            await manager.send_personal_message(
                json.dumps({"type": "error", "message": "Invalid card placement! Bad card counter increased."}), 
                player.id
            )
            await end_player_turn(room, player)
            return
        
        # Check for 6 on Ace penalty
        if (card_to_place.rank == 6 and target_player.visible_stack and 
            target_player.visible_stack[-1].rank == 14):
            target_player.bad_card_counter += 1
            await manager.send_personal_message(
                json.dumps({"type": "notification", "message": "You received 6 on Ace! Bad card counter increased."}), 
                target_player.id
            )
        
        # Place the card on another player's stack
        target_player.visible_stack.append(card_to_place)
        player.hand.remove(card_to_place)
        
        # Player can continue - they can draw again if deck exists
        await send_game_state(room)
    else:
        # Placing on own stack - this ends the turn automatically
        player.visible_stack.append(card_to_place)
        player.hand.remove(card_to_place)
        
        # Turn ends automatically when placing on own stack
        await end_player_turn(room, player)

async def handle_give_from_stack(room: GameRoom, player: Player, message: dict):
    """Handle giving card from player's own stack to another player"""
    if room.players[room.current_player_index].id != player.id:
        return
    
    target_player_id = message.get("target_player_id")
    
    if len(player.visible_stack) <= 1:
        return  # Can't give if only has base card
    
    target_player = room.get_player_by_id(target_player_id)
    if not target_player:
        return
    
    # Get top card from player's stack
    top_card = player.visible_stack[-1]
    
    # Check if move is valid
    if not room.can_stack_card(top_card, target_player.visible_stack):
        player.bad_card_counter += 1
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "Invalid card placement from stack! Bad card counter increased."}), 
            player.id
        )
        await end_player_turn(room, player)
        return
    
    # Check for 6 on Ace penalty
    if (top_card.rank == 6 and target_player.visible_stack and 
        target_player.visible_stack[-1].rank == 14):
        target_player.bad_card_counter += 1
        await manager.send_personal_message(
            json.dumps({"type": "notification", "message": "You received 6 on Ace! Bad card counter increased."}), 
            target_player.id
        )
    
    # Move the card
    card = player.visible_stack.pop()
    target_player.visible_stack.append(card)
    
    # Player continues their turn - they can give more from stack or draw from deck
    # Don't end turn automatically, let player decide next action
    await send_game_state(room)

async def end_player_turn(room: GameRoom, player: Player):
    """End current player's turn and move to next player"""
    # Reset turn flags
    player.has_drawn_this_turn = False
    player.cards_played_this_turn = 0
    
    # If player still has cards in hand, put them on their own stack
    while player.hand:
        card = player.hand.pop()
        player.visible_stack.append(card)
    
    # Move to next player
    room.current_player_index = (room.current_player_index + 1) % len(room.players)
    
    # Check if deck is empty - transition to donation phase
    if not room.deck:
        await transition_to_donation_phase(room)
    else:
        await send_game_state(room)

async def handle_beat_card(room: GameRoom, player: Player, message: dict):
    # Handle phase 2 card beating logic
    pass

async def handle_take_pile(room: GameRoom, player: Player):
    # Handle taking the battle pile
    pass

async def handle_donate_cards(room: GameRoom, player: Player, message: dict):
    # Handle card donation phase
    pass

async def transition_to_donation_phase(room: GameRoom):
    room.phase = GamePhase.DONATION
    
    # Give stacks to players
    for player in room.players:
        player.hand = player.visible_stack.copy()
        player.visible_stack = []
    
    await send_game_state(room)

async def send_game_state(room: GameRoom):
    for player in room.players:
        game_state = {
            "type": "game_state",
            "phase": room.phase.value,
            "players": [],
            "current_player_index": room.current_player_index,
            "trump_suit": room.trump_suit,
            "deck_size": len(room.deck),
            "battle_pile": [card.to_dict() for card in room.battle_pile],
            "player_id": player.id
        }
        
        # Add player info
        for p in room.players:
            player_info = {
                "id": p.id,
                "username": p.username,
                "ready": p.ready,
                "hand_size": len(p.hand),
                "visible_stack": [card.to_dict() for card in p.visible_stack],
                "bad_card_counter": p.bad_card_counter,
                "is_out": p.is_out
            }
            
            # Only show own hand
            if p.id == player.id:
                player_info["hand"] = [card.to_dict() for card in p.hand]
                player_info["hidden_cards"] = [card.to_dict() for card in p.hidden_cards]
            
            game_state["players"].append(player_info)
        
        await manager.send_personal_message(json.dumps(game_state), player.id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
