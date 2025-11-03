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
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('game_debug.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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
        logger.info(f"Card created: {self.suit} {self.rank}")
        
    def __dict__(self):
        return {"suit": self.suit, "rank": self.rank}
    
    def to_dict(self):
        result = {"suit": self.suit, "rank": self.rank}
        logger.info(f"Card.to_dict() returning: {result}")
        return result

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
        self.has_donated = False  # Add this line
        self.pending_donations = {}  # Add this line
        self.last_played_card = None  # Track last card played in phase 2
        self.has_picked_hidden_cards = False  # Track if hidden cards were picked up

class GameRoom:
    def __init__(self, room_id: str):
        self.id = room_id
        self.players: List[Player] = []
        self.phase = GamePhase.WAITING
        self.deck = []
        self.current_player_index = 0
        self.trump_suit = None
        self.last_card_player = None  # For showing last drawn card to other players (cleared after placement)
        self.last_deck_card_player = None  # Player who drew the LAST card from deck (for Phase 2 first turn)
        self.battle_pile = []
        self.last_drawn_card = None
        self.losers_from_previous_game = []  # Track losers for turn order
        self.drawn_cards_order = []  # Track order of drawn cards for trump determination
        
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
                card = Card(suit, rank)
                self.deck.append(card)
                if suit == 'hearts':  # Log hearts to see ranks
                    logger.info(f"create_deck: Created {suit} card with rank {rank}")
        logger.info(f"create_deck: Total deck size: {len(self.deck)}")
        random.shuffle(self.deck)
    
    def deal_initial_cards(self):
        """Deal hidden + 1 visible card to each player (losers get extra hidden cards)"""
        for player in self.players:
            # Base: 2 hidden cards
            num_hidden_cards = 2
            
            # Losers from previous game get additional hidden cards
            if player.id in self.losers_from_previous_game:
                # Find how many times this player lost
                loser_count = self.losers_from_previous_game.count(player.id)
                num_hidden_cards = 2 + loser_count  # 2 base + 1 per loss
            
            # Deal hidden cards
            player.hidden_cards = [self.deck.pop() for _ in range(num_hidden_cards)]
            # 1 visible card
            player.visible_stack = [self.deck.pop()]
    
    def can_stack_card(self, card: Card, target_stack: List[Card]) -> bool:
        """Check if card can be stacked on target (rank + 1 or 6 on Ace)"""
        if not target_stack:
            return True
        
        top_card = target_stack[-1]
        logger.info(f"can_stack_card: Trying to place {card.rank} on {top_card.rank}")
        
        # Normal stacking rule: card rank = top card rank + 1
        if card.rank == top_card.rank + 1:
            logger.info(f"can_stack_card: Normal seniority rule applies ({card.rank} on {top_card.rank})")
            return True
        
        # Special rule: 6 on Ace
        if card.rank == 6 and top_card.rank == 14:
            logger.info(f"can_stack_card: Special 6 on Ace rule applies")
            return True
        
        logger.info(f"can_stack_card: No seniority rule applies")
        return False
    
    def can_give_top_stack_card(self, player: Player) -> bool:
        """Check if player's top stack card can be given to any other player"""
        if not player.visible_stack or len(player.visible_stack) < 2:
            return False
        
        top_card = player.visible_stack[-1]
        card_underneath = player.visible_stack[-2]
        
        # Check if top card has seniority with card underneath
        # If yes, player is allowed to keep it (no penalty for drawing)
        if self.can_stack_card(top_card, [card_underneath]):
            logger.info(f"can_give_top_stack_card: Top card {top_card.rank} has seniority with card underneath {card_underneath.rank}, no penalty")
            return False
        
        # Check all other players
        for other_player in self.players:
            if other_player.id == player.id:
                continue
            
            if self.can_stack_card(top_card, other_player.visible_stack):
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
    
    def determine_trump_suit(self):
        """Determine trump suit from drawn cards order (last non-spade card)"""
        # Go through drawn cards in reverse order to find last non-spade
        for card in reversed(self.drawn_cards_order):
            if card.suit != 'spades':
                self.trump_suit = card.suit
                return
        
        # If all drawn cards were spades, trump is spades
        self.trump_suit = 'spades'
    
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
    
    # Check if player is joining mid-game or during waiting phase
    is_new_player = player.id not in [p.id for p in room.players]
    is_waiting_phase = room.phase == GamePhase.WAITING
    
    # Block new players from joining during active game
    if is_new_player and not is_waiting_phase:
        await websocket.close(code=1008, reason="Game in progress. Please wait for the current round to finish.")
        return
    
    if not room.add_player(player):
        await websocket.close(code=1000, reason="Room full")
        return
    
    # If new player joins during waiting phase (after a game), reset loser penalties
    if is_new_player and is_waiting_phase and hasattr(room, 'losers_from_previous_game') and room.losers_from_previous_game:
        room.losers_from_previous_game = []
        logger.info(f"New player {username} joined during waiting - loser penalties reset")
        await manager.broadcast_to_room(
            room_id,
            json.dumps({"type": "notification", "message": "New player joined! Hidden card penalties have been reset."})
        )
    
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
    
    elif action == "play_card" and room.phase == GamePhase.PHASE_TWO:
        await handle_play_card(room, player, message)
    
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
    room.battle_pile = []
    room.discarded_cards = []
    room.trump_suit = None
    room.drawn_cards_order = []
    room.last_drawn_card = None
    room.last_card_player = None
    room.last_deck_card_player = None
    room.current_player_index = 0
    
    # Clear donation tracker if it exists
    if hasattr(room, 'donation_tracker'):
        room.donation_tracker = {}
    
    # Reset all player states
    for player in room.players:
        player.ready = False  # Reset ready status for next round
        player.has_drawn_this_turn = False
        player.cards_played_this_turn = 0
        player.hand = []
        player.visible_stack = []
        player.hidden_cards = []
        player.bad_card_counter = 0
        player.is_out = False
        player.has_picked_hidden_cards = False
        player.last_played_card = None
        player.has_donated = False
        player.pending_donations = {}
        if hasattr(player, 'locked_stack_cards'):
            player.locked_stack_cards.clear()
    
    # Create deck and deal cards (with loser penalties if any)
    room.create_deck()
    room.deal_initial_cards()  # This handles losers_from_previous_game
    room.determine_first_player()
    
    await send_game_state(room)

async def handle_draw_card(room: GameRoom, player: Player):
    """Handle drawing a card from deck"""
    if room.players[room.current_player_index].id != player.id:
        return {"error": "Not your turn"}
    
    if not room.deck:
        return {"error": "Deck is empty"}
    
    if player.hand:  # Player already has a card in hand
        return {"error": "You already have a card in hand"}
    
    # PENALTY RULE 1: Check if top stack card could be given to others before drawing
    # Only applies if: stack has >= 2 cards AND top card has NO seniority with card underneath
    if len(player.visible_stack) >= 2:
        top_card = player.visible_stack[-1]
        underneath_card = player.visible_stack[-2]
        
        # Check if top card has seniority with underneath card
        has_seniority_with_underneath = (
            top_card.rank == underneath_card.rank + 1 or
            (top_card.rank == 6 and underneath_card.rank == 14)
        )
        
        # Only check penalty if top card does NOT have seniority with underneath
        if not has_seniority_with_underneath:
            # Check if top card could have been given to another player
            can_give_top_to_someone = False
            for target_player in room.players:
                if target_player.id != player.id:
                    if room.can_stack_card(top_card, target_player.visible_stack):
                        can_give_top_to_someone = True
                        break
            
            if can_give_top_to_someone:
                player.bad_card_counter += 1
                logger.info(f"{player.username} got bad card: drew from deck while top stack card could have been given to others")
    
    # Draw card from deck
    drawn_card = room.deck.pop()
    logger.info(f"handle_draw_card: Drew card from deck - rank {drawn_card.rank}, suit {drawn_card.suit}")
    
    player.hand = [drawn_card]  # Player can only have one card in hand
    room.last_drawn_card = drawn_card
    room.last_card_player = player  # Track who drew this card (for notification visibility)
    room.drawn_cards_order.append(drawn_card)  # Track order for trump determination
    
    logger.info(f"handle_draw_card: Player {player.username} now has in hand: {[(c.rank, c.suit) for c in player.hand]}")
    
    # Check if this was the last card - determine trump and track player for Phase 2 first turn
    if not room.deck:
        room.determine_trump_suit()
        room.last_deck_card_player = player  # This player goes first in Phase 2
    
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
            # Invalid move - increment bad card counter and place card on player's own stack
            player.bad_card_counter += 1
            player.visible_stack.append(card_to_place)
            player.hand.remove(card_to_place)
            
            # Clear last drawn card (card has been placed)
            room.last_drawn_card = None
            room.last_card_player = None
            
            await manager.send_personal_message(
                json.dumps({"type": "error", "message": "Invalid card placement! Card placed on your stack. Bad card counter increased."}), 
                player.id
            )
            await end_player_turn(room, player)
            return
        
        # PENALTY RULE 3: Check for 6 on Ace penalty (only receiver gets penalty)
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
        
        # Clear last drawn card (card has been placed)
        room.last_drawn_card = None
        room.last_card_player = None
        
        # Check if deck is empty - transition to donation phase
        if not room.deck:
            await transition_to_donation_phase(room)
        else:
            # Player can continue - they can draw again if deck exists
            await send_game_state(room)

    else:
    # Placing on own stack
        if room.can_stack_card(card_to_place, player.visible_stack):
            # Check 6 on Ace
            if (card_to_place.rank == 6 and player.visible_stack and 
                player.visible_stack[-1].rank == 14):
                player.bad_card_counter += 1
            # Turn continues when seniority rule applies on your own stack
            player.visible_stack.append(card_to_place)
            player.hand.remove(card_to_place)
            
            # Clear last drawn card (card has been placed)
            room.last_drawn_card = None
            room.last_card_player = None
            
            # Check if deck is empty after placing - transition to donation phase
            if not room.deck:
                await transition_to_donation_phase(room)
            else:
                await send_game_state(room)
        else:
            # Placing on own stack WITHOUT seniority rule
            # PENALTY RULE 2: Check if drawn card could have been given to others
            can_give_drawn_to_someone = False
            for target_player in room.players:
                if target_player.id != player.id:
                    if room.can_stack_card(card_to_place, target_player.visible_stack):
                        can_give_drawn_to_someone = True
                        break
            
            if can_give_drawn_to_someone:
                player.bad_card_counter += 1
                logger.info(f"{player.username} got bad card: could give drawn card to others but placed on own stack without seniority")
            
            # Turn ends automatically when placing on own stack and no seniority rule
            player.visible_stack.append(card_to_place)
            player.hand.remove(card_to_place)
            
            # Clear last drawn card (card has been placed)
            room.last_drawn_card = None
            room.last_card_player = None
            
            await end_player_turn(room, player)
            return

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
    
    # Check if this card is locked (player drew from deck instead of giving it)
    if hasattr(player, 'locked_stack_cards'):
        if (top_card.suit, top_card.rank) in player.locked_stack_cards:
            await manager.send_personal_message(
                json.dumps({"type": "error", "message": "This card is locked! You should have given it before drawing."}), 
                player.id
            )
            return
    
    # Check if move is valid
    if not room.can_stack_card(top_card, target_player.visible_stack):
        player.bad_card_counter += 1
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "Invalid card placement from stack! Bad card counter increased."}), 
            player.id
        )
        await end_player_turn(room, player)
        return
    
    # PENALTY RULE 3: Check for 6 on Ace penalty (only receiver gets penalty)
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
    
    # Check if deck is empty - transition to donation phase
    if not room.deck:
        await transition_to_donation_phase(room)
    else:
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

def can_beat_card(card: Card, target_card: Card, trump_suit: str) -> bool:
    """Check if card can beat target card in battle"""
    # 7 of spades beats everything
    if card.suit == 'spades' and card.rank == 7:
        return True
    
    # Can't beat 7 of spades unless with another spade
    if target_card.suit == 'spades' and target_card.rank == 7:
        return card.suit == 'spades'
    
    # Spades can only be beaten by spades
    if target_card.suit == 'spades':
        return card.suit == 'spades' and card.rank > target_card.rank
    
    # Same suit: higher rank or 6 beats Ace
    if card.suit == target_card.suit:
        if card.rank > target_card.rank:
            return True
        if card.rank == 6 and target_card.rank == 14:
            return True
        return False
    
    # Trump beats non-trump (unless target is spade)
    if card.suit == trump_suit and target_card.suit != trump_suit:
        return True
    
    return False

async def handle_play_card(room: GameRoom, player: Player, message: dict):
    """Handle playing a card to start or continue battle pile"""
    if room.players[room.current_player_index].id != player.id:
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "Not your turn"}),
            player.id
        )
        return
    
    if player.is_out:
        return
    
    card_data = message.get("card")
    if not card_data:
        return
    
    # Find card in player's hand
    card_to_play = None
    for hand_card in player.hand:
        if hand_card.suit == card_data["suit"] and hand_card.rank == card_data["rank"]:
            card_to_play = hand_card
            break
    
    if not card_to_play:
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "Card not in hand"}),
            player.id
        )
        return
    
    # If battle pile is empty, just add the card
    if not room.battle_pile:
        player.hand.remove(card_to_play)
        room.battle_pile.append(card_to_play)
        player.last_played_card = card_to_play
        logger.info(f"{player.username} started battle pile with {card_to_play.rank} of {card_to_play.suit}")
        
        # Check if player should pick hidden cards or win
        await check_player_status(room, player)
        
        # Check if game ended (phase changed to WAITING)
        if room.phase == GamePhase.WAITING:
            # Game is over, don't advance turn
            return
        
        # Move to next player
        room.current_player_index = (room.current_player_index + 1) % len(room.players)
        # Skip players who are out
        while room.players[room.current_player_index].is_out:
            room.current_player_index = (room.current_player_index + 1) % len(room.players)
        
        await send_game_state(room)
        return
    
    # Battle pile exists - must beat the top card
    top_card = room.battle_pile[-1]
    
    if not can_beat_card(card_to_play, top_card, room.trump_suit):
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "Card cannot beat the top card"}),
            player.id
        )
        return
    
    # Successfully beat the card
    player.hand.remove(card_to_play)
    room.battle_pile.append(card_to_play)
    player.last_played_card = card_to_play
    logger.info(f"{player.username} beat with {card_to_play.rank} of {card_to_play.suit}")
    
    # Check if pile should be discarded (size == number of active players)
    active_players = [p for p in room.players if not p.is_out]
    
    if len(room.battle_pile) >= len(active_players):
        logger.info(f"Battle pile complete ({len(room.battle_pile)} cards) - showing for 3 seconds before discarding")
        
        # First, send game state with the complete pile so all players can see it
        await send_game_state(room)
        
        # Wait 3 seconds so all players can see the winning card
        await asyncio.sleep(3)
        
        logger.info(f"Battle pile discarded ({len(room.battle_pile)} cards)")
        # Move cards to discarded pile
        if not hasattr(room, 'discarded_cards'):
            room.discarded_cards = []
        room.discarded_cards.extend(room.battle_pile)
        room.battle_pile = []
        
        # Check ALL active players' status after discard (for hidden cards pickup)
        # This is important in 2-player scenarios where both may have played their last card
        for active_player in active_players:
            await check_player_status(room, active_player)
        
        # Check if game ended after checking player status
        if room.phase == GamePhase.WAITING:
            # Game is over, don't continue
            return
        
        # Check if current player can continue (has cards and not out)
        current_player = room.players[room.current_player_index]
        if current_player.is_out or len(current_player.hand) == 0:
            # Current player cannot continue, advance to next player
            room.current_player_index = (room.current_player_index + 1) % len(room.players)
            # Skip players who are out (with safety check to prevent infinite loop)
            checked_count = 0
            while room.players[room.current_player_index].is_out and checked_count < len(room.players):
                room.current_player_index = (room.current_player_index + 1) % len(room.players)
                checked_count += 1
            
            # If all players are out, game should have ended - this is a safety check
            if checked_count >= len(room.players):
                logger.warning("All players are OUT after pile discard - game should have ended!")
                # Force check if game ended
                active_players_after = [p for p in room.players if not p.is_out]
                if len(active_players_after) == 0:
                    # This shouldn't happen, but handle gracefully
                    room.phase = GamePhase.WAITING
                    return
        
        # Continue with current or next player after discard
        await send_game_state(room)
        return
    
    # Pile not full yet, move to next player
    await check_player_status(room, player)
    
    # Check if game ended (phase changed to WAITING)
    if room.phase == GamePhase.WAITING:
        # Game is over, don't advance turn
        return
    
    room.current_player_index = (room.current_player_index + 1) % len(room.players)
    # Skip players who are out
    while room.players[room.current_player_index].is_out:
        room.current_player_index = (room.current_player_index + 1) % len(room.players)
    
    await send_game_state(room)

async def handle_take_pile(room: GameRoom, player: Player):
    """Handle taking the battle pile"""
    logger.info(f"handle_take_pile called by {player.username}")
    
    if room.players[room.current_player_index].id != player.id:
        logger.warning(f"{player.username} tried to take pile but not their turn")
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "Not your turn"}),
            player.id
        )
        return
    
    if player.is_out:
        logger.info(f"{player.username} is out, cannot take pile")
        return
    
    if not room.battle_pile:
        logger.info(f"Battle pile is empty, nothing to take")
        return
    
    # For 2 players: take all cards
    active_players = [p for p in room.players if not p.is_out]
    if len(active_players) == 2:
        # Take all cards from pile
        player.hand.extend(room.battle_pile)
        logger.info(f"{player.username} took {len(room.battle_pile)} cards from battle pile")
        room.battle_pile = []
        
        # Check all players for hidden card pickup (since pile is now empty)
        for p in room.players:
            await check_player_status(room, p)
        
        # Check if game ended after checking player status
        if room.phase == GamePhase.WAITING:
            # Game is over
            return
        
        # Move to next player
        room.current_player_index = (room.current_player_index + 1) % len(room.players)
        # Skip players who are out (with safety check)
        checked_count = 0
        while room.players[room.current_player_index].is_out and checked_count < len(room.players):
            room.current_player_index = (room.current_player_index + 1) % len(room.players)
            checked_count += 1
        
        await send_game_state(room)
        return
    
    # For 3+ players: take bottom card, leave rest
    if len(room.battle_pile) > 0:
        bottom_card = room.battle_pile.pop(0)
        player.hand.append(bottom_card)
        logger.info(f"{player.username} took bottom card: {bottom_card.rank} of {bottom_card.suit}")
        
        # Check all players for hidden card pickup (bottom card removed from pile)
        for p in room.players:
            await check_player_status(room, p)
        
        # Check if game ended after checking player status
        if room.phase == GamePhase.WAITING:
            # Game is over
            return
        
        # Move to next player
        room.current_player_index = (room.current_player_index + 1) % len(room.players)
        # Skip players who are out (with safety check)
        checked_count = 0
        while room.players[room.current_player_index].is_out and checked_count < len(room.players):
            room.current_player_index = (room.current_player_index + 1) % len(room.players)
            checked_count += 1
        
        await send_game_state(room)

async def check_player_status(room: GameRoom, player: Player):
    """Check if player should pick hidden cards or has won"""
    # Skip if player already out
    if player.is_out:
        return
    
    # Only check after player has played at least one card
    if not player.last_played_card:
        return
    
    # Check if player has no cards in hand
    if len(player.hand) == 0:
        # Player can only pick up hidden cards if battle pile was discarded (new pile started)
        # Battle pile must be empty for a new pile to have started
        new_pile_started = len(room.battle_pile) == 0
        
        # CRITICAL: Player can only win when their last played card has been DISCARDED (pile is empty)
        # If pile is not empty, player must wait for pile to resolve before winning
        # This prevents marking player OUT too early when they start a pile with their last card
        if new_pile_started:
            # First check if player has won (no cards in hand, no hidden cards, pile is empty)
            if len(player.hidden_cards) == 0 and player.has_picked_hidden_cards:
                # Player has won! (no cards in hand, no hidden cards, and already picked up hidden cards)
                player.is_out = True
                logger.info(f"{player.username} has won!")
                
                await manager.send_personal_message(
                    json.dumps({"type": "notification", "message": "You won! 🎉"}),
                    player.id
                )
                
                # Check if game is over
                active_players = [p for p in room.players if not p.is_out]
                if len(active_players) == 1:
                    # Last player loses
                    loser = active_players[0]
                    loser.is_out = True
                    logger.info(f"{loser.username} is the loser!")
                    
                    await manager.send_personal_message(
                        json.dumps({"type": "notification", "message": "You lost! You'll get +1 hidden card next round. 😔"}),
                        loser.id
                    )
                    
                    # Game finished - transition to WAITING for next round
                    room.phase = GamePhase.WAITING
                    # Append loser to list (accumulates across rounds)
                    if not hasattr(room, 'losers_from_previous_game'):
                        room.losers_from_previous_game = []
                    room.losers_from_previous_game.append(loser.id)
                    
                    # Reset all players to not ready for next round
                    for p in room.players:
                        p.ready = False
                    
                    # Send updated game state with WAITING phase
                    await send_game_state(room)
                    
                    # Notify all players game ended
                    await manager.broadcast_to_room(
                        room.id,
                        json.dumps({"type": "notification", "message": f"Game ended! {loser.username} lost and will get +1 hidden card next round. Click Ready to play again!"})
                    )
            elif len(player.hidden_cards) > 0 and not player.has_picked_hidden_cards:
                # Pick up hidden cards (stashed cards from phase 1) only when new pile started
                player.hand.extend(player.hidden_cards)
                player.hidden_cards = []
                player.has_picked_hidden_cards = True
                logger.info(f"{player.username} picked up {len(player.hand)} hidden cards")
                
                await manager.send_personal_message(
                    json.dumps({"type": "notification", "message": "You picked up your stashed cards!"}),
                    player.id
                )

async def handle_beat_card(room: GameRoom, player: Player, message: dict):
    """Alias for handle_play_card for backward compatibility"""
    await handle_play_card(room, player, message)

async def transition_to_donation_phase(room: GameRoom):
    """Transition from phase 1 to donation phase"""
    # Check if any player has bad card counter > 0
    players_needing_donations = [p for p in room.players if p.bad_card_counter > 0]
    
    if not players_needing_donations:
        # No one needs donations, skip directly to Phase 2
        logger.info("No players need donations, skipping donation phase")
        await transition_to_phase_two(room)
        return
    
    room.phase = GamePhase.DONATION
    logger.info(f"Starting donation phase. Players needing cards: {[p.username for p in players_needing_donations]}")
    
    # Move all visible_stack cards to hand for all players (so they can donate everything)
    for player in room.players:
        if player.visible_stack:
            player.hand.extend(player.visible_stack)
            player.visible_stack = []
        logger.info(f"{player.username}: {len(player.hand)} cards in hand, bad_card_counter={player.bad_card_counter}")
    
    # Track donations: {recipient_id: {donor_id: num_cards_donated}}
    room.donation_tracker = {}
    for player in players_needing_donations:
        room.donation_tracker[player.id] = {}
        for donor in room.players:
            if donor.id != player.id:
                room.donation_tracker[player.id][donor.id] = 0
    
    # Reset donation tracking and mark players who don't need to donate as done
    for player in room.players:
        player.pending_donations = {}
        # Auto-mark players who don't need to donate as done
        if not player_needs_to_donate(room, player) or len(player.hand) == 0:
            player.has_donated = True
            logger.info(f"{player.username} has no donations to make, auto-marked as done")
        else:
            player.has_donated = False
    
    # Find first player who needs to donate
    room.current_player_index = -1
    for i, player in enumerate(room.players):
        if not player.has_donated:
            room.current_player_index = i
            logger.info(f"First donor: {player.username} (index {i})")
            break
    
    # If no one needs to donate, skip directly to Phase 2
    if room.current_player_index == -1:
        logger.info("No valid donors found, skipping donation phase")
        await transition_to_phase_two(room)
        return
    
    await send_game_state(room)

def player_needs_to_donate(room: GameRoom, player: Player) -> bool:
    """Check if a player needs to donate cards to any recipients"""
    # Check if there are any recipients this player still needs to donate to
    for recipient_id, donors in room.donation_tracker.items():
        recipient = room.get_player_by_id(recipient_id)
        if not recipient or recipient.id == player.id:
            continue
        
        needed = recipient.bad_card_counter
        already_donated = donors.get(player.id, 0)
        
        if already_donated < needed:
            return True  # This player still needs to donate
    
    return False  # This player has no one to donate to

async def advance_donation_turn(room: GameRoom):
    """Advance to next player in donation phase or transition to phase 2 if complete"""
    # Check if donation phase is complete
    # Donations complete when all players have finished donating (marked as has_donated)
    # A player is marked has_donated when they either:
    # 1. Donated required amount to all recipients
    # 2. Ran out of cards (handled in handle_donate_cards when hand is empty)
    
    all_players_donated = all(player.has_donated for player in room.players)
    
    if all_players_donated:
        logger.info("All players have completed donations, transitioning to phase 2")
        await transition_to_phase_two(room)
    else:
        # Move to next player who needs to donate
        attempts = 0
        max_attempts = len(room.players)
        
        while attempts < max_attempts:
            room.current_player_index = (room.current_player_index + 1) % len(room.players)
            current_player = room.players[room.current_player_index]
            
            # Skip if player already completed donations
            if current_player.has_donated:
                attempts += 1
                continue
            
            # Skip if player has no one to donate to (auto-mark as done)
            if not player_needs_to_donate(room, current_player):
                logger.info(f"{current_player.username} has no one to donate to, auto-skipping")
                current_player.has_donated = True
                attempts += 1
                continue
            
            # Skip if player has no cards to donate (auto-mark as done)
            if len(current_player.hand) == 0:
                logger.info(f"{current_player.username} has no cards to donate, auto-skipping")
                current_player.has_donated = True
                attempts += 1
                continue
            
            # Found a player who needs to donate
            logger.info(f"Moving to next donator: {current_player.username} (index {room.current_player_index})")
            await send_game_state(room)
            return
        
        # All players processed, check if we should transition
        logger.info("All players checked, transitioning to phase 2")
        await transition_to_phase_two(room)

async def handle_donate_cards(room: GameRoom, player: Player, message: dict):
    """Handle card donation from player to players with bad cards"""
    if room.phase != GamePhase.DONATION:
        logger.info(f"Donation attempted in wrong phase: {room.phase}")
        return
    
    if room.players[room.current_player_index].id != player.id:
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "Not your turn to donate"}), 
            player.id
        )
        return
    
    donations = message.get("donations", {})  # {target_player_id: [card_indices]}
    logger.info(f"Player {player.username} donating: {donations}")
    
    # Player should have cards to donate (backend auto-skips players without cards)
    # Process each donation (should be only one recipient per call now)
    for target_player_id, card_indices in donations.items():
        target_player = room.get_player_by_id(target_player_id)
        
        # Check if target is valid (not self, exists, and needs cards)
        if not target_player:
            logger.warning(f"Invalid donation target: {target_player_id}")
            continue
        
        if target_player.id == player.id:
            logger.warning(f"Player {player.username} tried to donate to themselves")
            continue
        
        if target_player.bad_card_counter <= 0:
            logger.warning(f"Target player {target_player.username} doesn't need cards")
            continue
        
        # Check how many cards this donor still needs to give to this recipient
        needed = target_player.bad_card_counter
        already_donated = room.donation_tracker.get(target_player.id, {}).get(player.id, 0)
        can_donate = needed - already_donated
        
        if can_donate <= 0:
            continue
        
        # Sort indices in reverse to remove from end first (to maintain correct indices)
        card_indices_sorted = sorted(card_indices, reverse=True)
        cards_donated = 0
        
        for idx in card_indices_sorted:
            if 0 <= idx < len(player.hand) and cards_donated < can_donate:
                card = player.hand.pop(idx)
                target_player.hand.append(card)
                cards_donated += 1
                
                # Track donation
                if target_player.id not in room.donation_tracker:
                    room.donation_tracker[target_player.id] = {}
                room.donation_tracker[target_player.id][player.id] = already_donated + cards_donated
                
                logger.info(f"Donated {card.rank} of {card.suit} from {player.username} to {target_player.username}")
    
    # Check if this donor has completed all required donations
    # A donor is done when they have donated to ALL recipients who need cards
    all_donations_complete = True
    for recipient_id, donors in room.donation_tracker.items():
        recipient = room.get_player_by_id(recipient_id)
        if not recipient or recipient.id == player.id:
            continue
        
        needed = recipient.bad_card_counter
        already_donated = donors.get(player.id, 0)
        
        if already_donated < needed:
            all_donations_complete = False
            break
    
    if all_donations_complete:
        # This donor has completed all donations
        player.has_donated = True
        # Advance to next donor or complete donation phase
        await advance_donation_turn(room)
    else:
        # This donor still needs to donate to other recipients
        # Send updated game state so they see the updated hand and next recipient
        await send_game_state(room)

async def transition_to_phase_two(room: GameRoom):
    """Transition to phase 2 (battle phase)"""
    logger.info("Transitioning to Phase 2 (Battle)")
    room.phase = GamePhase.PHASE_TWO
    
    # Reveal trump suit (it was determined when last card was drawn in phase 1)
    if not room.trump_suit and room.drawn_cards_order:
        room.determine_trump_suit()
        logger.info(f"Trump suit determined: {room.trump_suit}")
    
    # Move all visible_stack cards to player hands (only for players who still have cards in stacks)
    for player in room.players:
        if player.visible_stack:
            player.hand.extend(player.visible_stack)
            player.visible_stack = []
        logger.info(f"Player {player.username} now has {len(player.hand)} cards in hand")
    
    # Check if any players have empty hands and need to pick up hidden cards immediately
    for player in room.players:
        if not player.hand and player.hidden_cards and not player.has_picked_hidden_cards:
            # Player donated all cards, pick up hidden cards now
            player.hand.extend(player.hidden_cards)
            player.hidden_cards = []
            player.has_picked_hidden_cards = True
            logger.info(f"{player.username} donated all cards, picked up {len(player.hand)} hidden cards at start of Phase 2")
    
    # Reset bad card counters (donations already handled in donation phase)
    for player in room.players:
        player.bad_card_counter = 0
    
    # Determine first player (who drew last card from deck in phase 1)
    if room.last_deck_card_player:
        last_player_index = next((i for i, p in enumerate(room.players) if p.id == room.last_deck_card_player.id), 0)
        room.current_player_index = last_player_index
        logger.info(f"First player in Phase 2: {room.players[room.current_player_index].username} (drew last deck card)")
    else:
        # Fallback to first player if no last_deck_card_player is set
        room.current_player_index = 0
        logger.info(f"No last deck card player found, defaulting to first player")
    
    # Reset battle pile
    room.battle_pile = []
    
    await send_game_state(room)

async def send_game_state(room: GameRoom):
    for player in room.players:
        # Show last drawn card to ALL players in phase_one
        show_last_drawn = bool(room.last_drawn_card)
        
        game_state = {
            "type": "game_state",
            "phase": room.phase.value,
            "players": [],
            "current_player_index": room.current_player_index,
            "trump_suit": room.trump_suit,
            "deck_size": len(room.deck),
            "battle_pile": [card.to_dict() for card in room.battle_pile],
            "discarded_count": len(getattr(room, 'discarded_cards', [])),
            "last_drawn_card": room.last_drawn_card.to_dict() if show_last_drawn else None,
            "player_id": player.id,
            "players_needing_donations": [p.id for p in room.players if p.bad_card_counter > 0],
            "donation_tracker": getattr(room, 'donation_tracker', {})  # Include donation progress
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
                "is_out": p.is_out,
                "has_donated": getattr(p, 'has_donated', False),
                "has_picked_hidden_cards": p.has_picked_hidden_cards,
                "hidden_cards_count": len(p.hidden_cards)  # Show count to everyone
            }
            
            # Only show own hand and locked cards
            if p.id == player.id:
                player_info["hand"] = [card.to_dict() for card in p.hand]
                player_info["hidden_cards"] = [card.to_dict() for card in p.hidden_cards]
                # Send locked stack cards info
                if hasattr(p, 'locked_stack_cards'):
                    player_info["locked_stack_cards"] = [{"suit": suit, "rank": rank} for suit, rank in p.locked_stack_cards]
                else:
                    player_info["locked_stack_cards"] = []
            
            game_state["players"].append(player_info)
        
        await manager.send_personal_message(json.dumps(game_state), player.id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
