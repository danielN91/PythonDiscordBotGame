import random
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from Game.Models.Monster import Monster
from Game.Models.Player import Player
from Game.Database.database import Database
from Game.Managers.player_db_connection import handle_player_death, update_player_rewards, update_player_hp
from Game.Managers.loot_db_connection import get_all_loot
import interactions

RAID_COOLDOWN_SECONDS = 60  # Adjust this value as needed

class CombatSystem:
    @staticmethod
    def calculate_power_score(entity: Player | Monster, is_player: bool = True) -> float:
        """Calculate power score for either player or monster"""
        base_score = 0
        
        if is_player:
            # Base stats contribution
            base_score += entity.stats["strength"] * 1.0
            base_score += entity.stats["agility"] * 1.0
            base_score += entity.stats["vitality"] * 1.0
            base_score += entity.level * 5
            
            # Equipment bonus (we can expand this later)
            equipment_bonus = entity.calculate_equipment_power()
            base_score += equipment_bonus
        else:
            # Monster power calculation
            base_score += entity.level * 6
            base_score += entity.damage * 1.3
            base_score += entity.defense
            
            # Rarity multipliers
            rarity_multipliers = {
                "E": 1.0,
                "D": 1.3,
                "C": 1.6,
                "B": 2.0,
                "A": 2.5,
                "S": 3.0
            }
            base_score *= rarity_multipliers.get(entity.rarity, 1.0)
        
        # Add randomness factor (mindset variable)
        mindset = random.uniform(0, 10)
        final_score = base_score * (1 + (mindset / 20))  # Mindset can affect up to ±50%
        
        return final_score

class RaidManager:
    def __init__(self):
        self.combat_system = CombatSystem()

    async def generate_monsters(self, tower_level: int, player_level: int) -> List[Monster]:
        db = Database()
        monsters_collection = db.get_monsters_collection()
        

        # Calculate level range for monsters
        min_monster_level = max(1, player_level - 2)
        max_monster_level = player_level + 2

        # Query monsters within the player's level range
        monster_query = {
            "level": {"$gte": min_monster_level, "$lte": max_monster_level}
        }
        
        potential_monsters = list(monsters_collection.find(monster_query))
        
        # Randomly select monsters
        selected_monsters = []
        num_monsters = random.randint(3, min(len(potential_monsters), 7))
        selected_monster_data = random.sample(potential_monsters, num_monsters)
        
        for monster_data in selected_monster_data:
            monster = Monster(
                monster_id=monster_data["monster_id"],
                name=monster_data["name"],
                level=monster_data["level"],
                rarity=monster_data["rarity"],
                monster_type=monster_data.get("monster_type", "generic"),
                hp=monster_data.get("hp", 50),
                damage=monster_data.get("damage", 5),
                defense=monster_data.get("defense", 3),
                experience_reward=monster_data.get("experience_reward", 15),
                gold_reward=monster_data.get("gold_reward", 10),
                loot_table=monster_data.get("loot_table", [])
            )
            
            selected_monsters.append(monster)
        
        return selected_monsters

    def generate_monster_loot(self, monster_level: int) -> Dict[str, dict]:
        """
        Generate loot rewards based on monster level
        Returns a dictionary of loot_id: {quantity: int, name: str}
        """
        loot_rewards = {}
        all_loot = get_all_loot()
        
        # Filter available loot based on monster level
        # Every 10 levels unlocks new loot tier
        # Level 1-9: L001
        # Level 10-19: L001, L002
        # Level 20-29: L001, L002, L003, etc.
        max_loot_tier = (monster_level // 10) + 1
        available_loot = [
            loot for loot in all_loot 
            if int(loot['id'].replace('L', '')) <= max_loot_tier
        ]
        
        if not available_loot:
            return {}
            
        # Calculate max items based on monster level
        max_items = min(monster_level // 5, 5)  # Cap at 5 items, 1 item per 5 levels
        num_items = random.randint(1, max_items) if max_items > 0 else 0
        
        # Generate random loot from available items
        for _ in range(num_items):
            if available_loot:
                chosen_loot = random.choice(available_loot)
                loot_id = chosen_loot['id']
                if loot_id in loot_rewards:
                    loot_rewards[loot_id]['quantity'] += 1
                else:
                    loot_rewards[loot_id] = {
                        'quantity': 1,
                        'name': chosen_loot['name']
                    }
                
        return loot_rewards

    async def process_battle(self, player: Player, monster: Monster) -> Dict:
        player_power = self.combat_system.calculate_power_score(player, True)
        monster_power = self.combat_system.calculate_power_score(monster, False)
            
        player_wins = player_power > monster_power
        
        rewards = {
            "experience": monster.experience_reward if player_wins else 0,
            "loot": {}  # Initialize empty loot dictionary
        }
        
        if player_wins:
            rewards["loot"] = self.generate_monster_loot(monster.level)
            # Add loot to player's inventory
            for loot_id, loot_info in rewards["loot"].items():
                player.add_loot(loot_id, loot_info['quantity'])
        
        damage_taken = monster.damage if not player_wins else 0
        
        return {
            "player_won": player_wins,
            "damage_taken": damage_taken,
            "rewards": rewards,
            "monster_name": monster.name
        }

    async def process_raid(self, player: Player, tower_level: int) -> Dict:
        monsters = await self.generate_monsters(tower_level, player.level)
        
        raid_results = {
            "monsters_defeated": [],
            "monsters_defeated_by": [],
            "total_rewards": {"experience": 0, "loot": {}},  # Modified to handle loot
            "battles": [],
            "raid_complete": False,
            "player_survived": True,
            "damage_taken": 0,
            "max_hp": player.max_hp,
            "current_hp": player.current_hp
        }
        
        for monster in monsters:
            if player.current_hp <= 0:
                raid_results["player_survived"] = False
                break
                    
            battle_result = await self.process_battle(player, monster)
            raid_results["battles"].append(battle_result)
            
            if battle_result["player_won"]:
                raid_results["monsters_defeated"].append(monster.name)
                raid_results["total_rewards"]["experience"] += battle_result["rewards"]["experience"]
                
                # Aggregate loot rewards
                for loot_id, loot_info in battle_result["rewards"]["loot"].items():
                    if loot_id in raid_results["total_rewards"]["loot"]:
                        raid_results["total_rewards"]["loot"][loot_id]['quantity'] += loot_info['quantity']
                    else:
                        raid_results["total_rewards"]["loot"][loot_id] = loot_info
            else:
                raid_results["monsters_defeated_by"].append(monster.name)
                raid_results["damage_taken"] += battle_result["damage_taken"]
                if player.current_hp <= 0:
                    break
        
        raid_results["raid_complete"] = len(raid_results["monsters_defeated"]) == len(monsters)
        raid_results["current_hp"] = player.current_hp
        raid_results["max_hp"] = player.max_hp
        return raid_results

def create_raid_summary(results: Dict) -> str:
    summary = "🗡️ **Raid Summary** 🗡️"
    
    summary += f"\n\u2764\ufe0f  **Health:** {results['current_hp']} / {results['max_hp']}\n"
    summary += "\n**Monsters Defeated:**\n"
    for monster_id in results["monsters_defeated"]:
        summary += f"✅ {monster_id}\n"
    
    if results["monsters_defeated_by"]:
        summary += "\n**Monsters that Defeated You:**\n"
        for monster_id in results["monsters_defeated_by"]:
            summary += f"❌ {monster_id}\n"
    
    summary += "\n**Rewards:**\n"
    summary += f"✨ Experience: {results['total_rewards']['experience']:.0f}\n"
    
    if results['total_rewards']['loot']:
        summary += "\n**Loot Acquired:**\n"
        for loot_id in results['total_rewards']['loot']:
            loot_info = results['total_rewards']['loot'][loot_id]
            summary += f"🎁 {loot_info['name']}: {loot_info['quantity']}x\n"
    
    if results["raid_complete"]:
        summary += "\n🏆 Raid Complete! 🏆"
    elif results["player_survived"]:
        summary += "\n⚠️ Raid Abandoned - Retreated safely"
    else:
        summary += "\n💀 Raid Failed - Player Defeated"
    
    return summary


async def handle_raid_command(player: Player, ctx: interactions.SlashContext = None):
    # Check cooldown using new player method
    can_raid, remaining_cooldown, actual_cooldown = player.check_raid_cooldown()
    
    if not can_raid:
        if ctx:  # Only send message if context is provided
            await ctx.send(
                f"You must wait {remaining_cooldown:.1f}s before raiding again.\n"f"Actual cooldown: {actual_cooldown:.1f}s",
                ephemeral=True
            )
        return None

    # Update last raid time
    player.last_raid_time = datetime.utcnow()

    # Process raid
    raid_manager = RaidManager()
    results = await raid_manager.process_raid(player, player.tower_level)
    
    # Check player health after raid
    if player.current_hp <= 0:
        death_summary = handle_player_death(player)
        summary = create_raid_summary(results)
        summary += "\n\n**Death Consequences:**"
        summary += f"\n💀 HP Restored: {death_summary['hp_restored']}"
        summary += f"\n💰 Gold Lost: {death_summary['gold_lost']}"
        
        return summary
    
    # If player survived, process rewards
    reward_update = update_player_rewards(
        player,
        results["total_rewards"]["experience"],
        results
    )
    
    # Generate raid summary
    summary = create_raid_summary(results)
    
    # Add level up message if applicable
    if reward_update["leveled_up"]:
        summary += f"\n🎉 **Level Up!** You are now level {reward_update['new_level']}! 🎉"
    
    return summary