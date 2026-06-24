import httpx
import asyncio

class MinecraftController:
    def __init__(self, bridge_url="http://localhost:3001"):
        self.url = bridge_url
        self.client = httpx.AsyncClient()

    async def send_action(self, action_type, params={}):
        """Sends a physical movement command to the Node.js Mineflayer bot."""
        try:
            response = await self.client.post(
                f"{self.url}/action", 
                json={"action": action_type, "params": params},
                timeout=2.0
            )
            return response.json()
        except Exception as e:
            print(f"[MC Controller Error]: Couldn't reach Node.js bridge — {e}")
            return None

    async def get_state(self):
        """Fetches what Luna sees around her (nearby players, health, position)."""
        try:
            response = await self.client.get(f"{self.url}/state", timeout=1.0)
            return response.json()
        except Exception:
            return None