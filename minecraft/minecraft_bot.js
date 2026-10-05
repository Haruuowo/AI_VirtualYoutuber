const mineflayer = require('mineflayer')
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const http = require('http')
const express = require('express')

const app = express()
app.use(express.json())

const fs = require('fs')
const path = require('path')

// Load .env from project root
const envPath = path.resolve(__dirname, '../.env')
if (fs.existsSync(envPath)) {
  const envContent = fs.readFileSync(envPath, 'utf8')
  envContent.split('\n').forEach(line => {
    const trimmed = line.trim()
    if (trimmed && !trimmed.startsWith('#') && trimmed.includes('=')) {
      const [key, ...vals] = trimmed.split('=')
      if (key && !process.env[key.trim()]) {
        process.env[key.trim()] = vals.join('=').trim()
      }
    }
  })
}

// ── Config ────────────────────────────────────────────────────
const BOT_CONFIG = {
  host: process.env.MC_HOST || 'Haruuuuuowo-obpy.exaroton.me',
  port: parseInt(process.env.MC_PORT || '64559', 10),
  username: process.env.MC_USERNAME || 'Luna',
  version: process.env.MC_VERSION || false,
  auth: 'offline'
}

const BRIDGE_PORT = 3001

// ── State ─────────────────────────────────────────────────────
let bot = null
let eventQueue = []
let currentAction = 'idle'
let isPerformingAction = false

let companionMode = {
  active: false,
  leader: 'kairuuowo',
  isBusy: false,
  lastTorchTime: 0,
  lastMineTime: 0,
  lastDropCollectTime: 0
}

const HOSTILE_MOBS = [
  'zombie', 'skeleton', 'creeper', 'spider', 'cave_spider', 'enderman',
  'witch', 'blaze', 'slime', 'magma_cube', 'drowned', 'husk', 'stray',
  'phantom', 'pillager', 'vindicator', 'ravager', 'piglin_brute',
  'wither_skeleton', 'ghast', 'silverfish'
]

const ORE_BLOCKS = [
  'coal_ore', 'deepslate_coal_ore',
  'iron_ore', 'deepslate_iron_ore',
  'copper_ore', 'deepslate_copper_ore',
  'gold_ore', 'deepslate_gold_ore',
  'redstone_ore', 'deepslate_redstone_ore',
  'lapis_ore', 'deepslate_lapis_ore',
  'diamond_ore', 'deepslate_diamond_ore',
  'emerald_ore', 'deepslate_emerald_ore',
  'nether_quartz_ore', 'nether_gold_ore', 'ancient_debris',
  'raw_iron_block', 'raw_copper_block', 'raw_gold_block',
  'amethyst_cluster'
]

const MINABLE_STONE_BLOCKS = [
  'stone', 'cobblestone', 'deepslate', 'cobbled_deepslate',
  'diorite', 'andesite', 'granite', 'tuff'
]

// ── Event helpers ─────────────────────────────────────────────
function pushEvent(type, data = {}) {
  eventQueue.push({ type, data, timestamp: Date.now() })
  console.log(`[event] ${type}`, data)
}

function getMovements() {
  if (!bot || !bot.entity || !bot.entity.position) return null
  const mcData = require('minecraft-data')(bot.version)
  const m = new Movements(bot, mcData)
  m.canDig = false
  m.scafoldingBlocks = [] // Strictly disable scaffolding placements to prevent pathfinder place errors
  m.allowParkour = true // Allow 1-block jumps across gaps and terrain steps
  m.allowSprinting = true
  m.canSwim = true
  m.liquidCost = 300 // Strictly avoid walking into irrigation water holes
  m.dontCreateJumpingPath = false // Must be false so she can climb and jump over 1-block elevation!

  if (mcData.blocksByName.water) {
    m.blocksToAvoid.add(mcData.blocksByName.water.id)
  }
  if (mcData.blocksByName.flowing_water) {
    m.blocksToAvoid.add(mcData.blocksByName.flowing_water.id)
  }
  if (mcData.blocksByName.farmland) {
    m.blocksCantBreak.add(mcData.blocksByName.farmland.id)
  }
  return m
}

// ── Bot setup ─────────────────────────────────────────────────
function createBot() {
  bot = mineflayer.createBot(BOT_CONFIG)
  bot.loadPlugin(pathfinder)

  bot.once('spawn', () => {
    console.log('Bot spawned in world at', bot.entity.position)
    pushEvent('spawned', { position: bot.entity.position })
    const m = getMovements()
    if (m) bot.pathfinder.setMovements(m)
  })

  // Auto-swim in water & auto-jump over 1-block ledges
  bot.on('physicTick', () => {
    if (!bot || !bot.entity) return
    if (bot.entity.isInWater) {
      bot.setControlState('jump', true)
    } else if (bot.entity.isCollidedHorizontally && (bot.pathfinder?.isMoving() || currentAction !== 'idle')) {
      // Step-up jump: if colliding with an obstacle block while navigating, jump over it!
      bot.setControlState('jump', true)
    }
  })

  bot.on('path_update', (r) => {
    console.log(`[Pathfinder] Path status: ${r.status}`)
  })

  bot.on('path_reset', (reason) => {
    console.log(`[Pathfinder] Path reset: ${reason}`)
  })

  bot.on('goal_reached', () => {
    console.log('[Pathfinder] Reached destination.')
    isPerformingAction = false
    currentAction = 'idle'
    pushEvent('arrived', {})
  })

  let combatInterval = null

  async function retaliate(attacker) {
    if (!attacker || !bot || !bot.entity) return
    const leaderName = (companionMode.leader || 'kairuuowo').toLowerCase()

    // 0. FRIENDLY FIRE PROTECTION: Never attack companion leader or ally player!
    if (attacker.type === 'player' && attacker.username && (
      attacker.username.toLowerCase() === leaderName ||
      attacker.username.toLowerCase().includes(leaderName)
    )) {
      console.log(`[Combat Reflex] Friendly fire check: target is leader ${attacker.username}. Refusing to retaliate against leader!`)
      return
    }

    const attackerName = attacker.username || attacker.name || 'someone'
    console.log(`[Combat Reflex] Retaliating against: ${attackerName}`)

    // 1. Equip best melee weapon if available
    try {
      const weapon = bot.inventory.items().find(i => i.name.includes('sword') || i.name.includes('axe'))
      if (weapon) {
        await bot.equip(weapon, 'hand')
      }
    } catch (e) {}

    currentAction = 'retaliating'
    isPerformingAction = true

    if (combatInterval) clearInterval(combatInterval)

    // 2. Chase and strike
    try {
      bot.pathfinder.setGoal(new goals.GoalFollow(attacker, 2), true)
    } catch (e) {}

    let strikes = 0
    combatInterval = setInterval(async () => {
      const target = bot.entities[attacker.id]
      if (!target || strikes >= 8 || currentAction === 'flee') {
        clearInterval(combatInterval)
        combatInterval = null
        isPerformingAction = false
        currentAction = 'idle'
        return
      }

      const dist = bot.entity.position.distanceTo(target.position)
      if (dist < 4.0) {
        try {
          await bot.lookAt(target.position.offset(0, (target.height || 1.6) * 0.8, 0))
          bot.attack(target)
          strikes++
          console.log(`[Combat] Struck ${attackerName}! (Strike ${strikes}/8)`)
        } catch (err) {}
      }
    }, 550)
  }

  bot.on('entityHurt', async (entity) => {
    if (entity === bot.entity) {
      const hp = Math.round(bot.health)
      pushEvent('took_damage', { health: hp })

      const leaderName = (companionMode.leader || 'kairuuowo').toLowerCase()

      // 1. First priority: Check for hostile mobs in combat range (within 14m)
      const hostileMobs = Object.values(bot.entities)
        .filter(e => e && e !== bot.entity && e.position && e.name && HOSTILE_MOBS.includes(e.name.toLowerCase()))
        .sort((a, b) => bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position))

      const nearestHostile = hostileMobs[0]
      if (nearestHostile && bot.entity.position.distanceTo(nearestHostile.position) <= 14.0) {
        const mobName = nearestHostile.name
        console.log(`[Combat] Luna took damage! Engaging hostile mob ${mobName} at distance ${bot.entity.position.distanceTo(nearestHostile.position).toFixed(1)}m`)
        pushEvent('attacked_by', { attacker: mobName })
        await retaliate(nearestHostile)
        return
      }

      // 2. Second priority: Check for rival/enemy players (strictly excluding companion leader/Kai)
      const enemyPlayers = Object.values(bot.entities)
        .filter(e => e && e !== bot.entity && e.type === 'player' && e.position && e.username && !e.username.toLowerCase().includes(leaderName))
        .sort((a, b) => bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position))

      const nearestEnemy = enemyPlayers[0]
      if (nearestEnemy && bot.entity.position.distanceTo(nearestEnemy.position) < 5.0) {
        console.log(`[Combat] Luna attacked by rival player ${nearestEnemy.username}! Retaliating...`)
        pushEvent('attacked_by', { attacker: nearestEnemy.username })
        await retaliate(nearestEnemy)
        return
      }

      // 3. If companion leader is nearby when damage occurred (e.g. playful tap, accidental swing, or environmental damage)
      const nearbyLeader = Object.values(bot.entities).find(e => 
        e.type === 'player' && e.username && e.username.toLowerCase().includes(leaderName) && e.position &&
        bot.entity.position.distanceTo(e.position) < 4.5
      )
      if (nearbyLeader) {
        console.log(`[Combat] Luna took damage near companion leader ${nearbyLeader.username} - Friendly fire protected (will not attack leader).`)
        pushEvent('hurt_near_leader', { leader: nearbyLeader.username })
      }
    } else if (companionMode.active && entity.type === 'player' && entity.username && entity.username.toLowerCase().includes(companionMode.leader)) {
      // Leader took damage while in companion mode! Defend them immediately!
      const nearbyHostile = Object.values(bot.entities).find(e => 
        e.name && HOSTILE_MOBS.includes(e.name.toLowerCase()) && e.position &&
        entity.position.distanceTo(e.position) <= 10.0
      )
      if (nearbyHostile) {
        console.log(`[Companion Defend] Leader ${entity.username} was attacked by ${nearbyHostile.name}! Intercepting...`)
        await retaliate(nearbyHostile)
      }
    }
  })

  bot.on('entitySpawn', (entity) => {
    const hostile = ['creeper', 'zombie', 'skeleton', 'spider', 'enderman', 'witch', 'blaze']
    if (entity.name && hostile.includes(entity.name)) {
      const dist = bot.entity.position.distanceTo(entity.position)
      if (dist < 16) pushEvent('hostile_nearby', { mob: entity.name, distance: Math.round(dist) })
    }
  })

  // Detect when another player crouches/sneaks at Luna and crouch back!
  bot.on('entityUpdate', (entity) => {
    if (!bot || !bot.entity || isCrouching) return
    if (entity.type === 'player' && entity !== bot.entity && entity.position) {
      const dist = bot.entity.position.distanceTo(entity.position)
      if (dist <= 3.8) {
        const isSneaking = (entity.metadata && (entity.metadata[0] & 0x02) !== 0) || entity.crouching
        if (isSneaking && (Date.now() - lastFriendlyCrouchTime > 6000)) {
          lastFriendlyCrouchTime = Date.now()
          console.log(`[Friendly] Player ${entity.username || 'friend'} crouched at Luna! Crouching back!`)
          bot.lookAt(entity.position.offset(0, 1.5, 0)).catch(() => {})
          performFriendlyCrouch(3).catch(() => {})
        }
      }
    }
  })

  bot.on('death', () => {
    console.log('[Bot] Luna died. Auto-respawning in 1.2s...')
    pushEvent('died', {})
    isPerformingAction = false
    currentAction = 'idle'
    setTimeout(() => {
      try {
        if (bot) bot.respawn()
        console.log('[Bot] Luna respawned!')
      } catch (e) {
        console.error('[Bot] Respawn error:', e.message)
      }
    }, 1200)
  })

  bot.on('end', () => {
    console.log('Disconnected, reconnecting in 5s...')
    pushEvent('disconnected', {})
    setTimeout(createBot, 5000)
  })

  bot.on('chat', (username, message) => {
    if (username === bot.username) return
    pushEvent('chat', { username, message })
  })

  bot.on('health', () => {
    pushEvent('health_changed', { health: bot.health, food: bot.food })
    if (bot.food < 18 || bot.health < 16) {
      autoEat().catch(() => {})
    }
  })

  bot.on('kicked', (reason) => {
    console.log(`Kicked from server: ${reason}`)
    pushEvent('disconnected', { reason })
  })

  bot.on('error', (err) => {
    console.error('Mineflayer Error:', err)
  })
}

// ── Auto-Eat Helper ───────────────────────────────────────────
async function autoEat() {
  if (!bot || !bot.inventory) return
  if (bot.food >= 20 && bot.health >= 19) return

  const edibleNames = [
    'cooked_beef', 'steak', 'cooked_porkchop', 'cooked_mutton',
    'cooked_chicken', 'bread', 'golden_carrot', 'golden_apple',
    'apple', 'baked_potato', 'carrot', 'sweet_berries', 'melon_slice'
  ]

  const foodItem = bot.inventory.items().find(i => edibleNames.includes(i.name))
  if (foodItem) {
    try {
      console.log(`[Auto-Eat] Eating ${foodItem.name}...`)
      await bot.equip(foodItem, 'hand')
      await bot.consume()
      console.log(`[Auto-Eat] Restored! Health: ${bot.health}, Food: ${bot.food}`)
    } catch (e) {
      // Ignored if busy or already eating
    }
  }
}

// ── Farming Helpers ───────────────────────────────────────────
function isMatureCrop(block) {
  if (!block) return false
  const name = block.name
  const props = block._properties || {}
  const age = props.age !== undefined ? parseInt(props.age, 10) : block.metadata

  if ((name === 'wheat' || name === 'carrots' || name === 'potatoes') && age >= 7) return true
  if (name === 'beetroots' && age >= 3) return true
  if (name === 'sweet_berry_bush' && age >= 2) return true
  if (name === 'melon' || name === 'pumpkin' || name === 'sugar_cane') return true
  return false
}

function getSeedForCrop(cropName) {
  if (!bot || !bot.inventory) return null
  const seedMap = {
    'wheat': ['wheat_seeds', 'seeds'],
    'carrots': ['carrot', 'carrots'],
    'potatoes': ['potato', 'potatoes'],
    'beetroots': ['beetroot_seeds'],
    'sweet_berries': ['sweet_berries']
  }
  const matching = seedMap[cropName] || ['wheat_seeds', 'seeds', 'carrot', 'potato']
  return bot.inventory.items().find(i => matching.includes(i.name))
}

// ── Fast Harvesting Helpers ────────────────────────────────────
async function harvestAndReplant(cropBlock) {
  if (!cropBlock || !cropBlock.position || !isMatureCrop(cropBlock)) return false
  const cropName = cropBlock.name
  const farmlandPos = new Vec3(cropBlock.position.x, cropBlock.position.y - 1, cropBlock.position.z)
  const farmland = bot.blockAt(farmlandPos)

  if (cropName === 'sweet_berry_bush') {
    try {
      await bot.activateBlock(cropBlock)
      return true
    } catch (e) {
      return false
    }
  }

  try {
    await bot.dig(cropBlock)
  } catch (e) {
    return false
  }

  // Quick replant matching seed on farmland underneath
  const seedItem = getSeedForCrop(cropName)
  if (seedItem && farmland && farmland.name === 'farmland') {
    try {
      await bot.equip(seedItem, 'hand')
      await bot.placeBlock(farmland, new Vec3(0, 1, 0))
    } catch (err) {}
  }
  return true
}

async function collectDropsNearby(radius = 8) {
  if (!bot || !bot.entity) return
  const drops = Object.values(bot.entities).filter(e => 
    (e.name === 'item' || e.type === 'object') && 
    bot.entity.position.distanceTo(e.position) <= radius
  )
  for (const drop of drops.slice(0, 8)) {
    if (bot.entities[drop.id]) {
      try {
        await bot.pathfinder.goto(new goals.GoalNearXZ(drop.position.x, drop.position.z, 0.5))
      } catch (e) {}
    }
  }
}

async function escapeWater() {
  if (!bot || !bot.entity || !bot.entity.isInWater) return
  const dryBlock = bot.findBlock({
    matching: (b) => b.name !== 'water' && b.name !== 'flowing_water' && b.name !== 'air',
    maxDistance: 8
  })
  if (dryBlock) {
    console.log(`[Water Escape] Swimming out of water towards ${dryBlock.name} at ${dryBlock.position}...`)
    try {
      bot.setControlState('jump', true)
      await bot.pathfinder.goto(new goals.GoalNearXZ(dryBlock.position.x, dryBlock.position.z, 1))
    } catch (e) {}
    bot.setControlState('jump', false)
  }
}

// ── Chest Storage Helper ──────────────────────────────────────
async function depositInChest() {
  if (!bot || !bot.inventory) return false

  const chestBlock = bot.findBlock({
    matching: (b) => b.name === 'chest' || b.name === 'trapped_chest' || b.name === 'barrel',
    maxDistance: 24
  })

  if (!chestBlock) {
    console.log('[Chest] No chest or barrel found nearby within 24 blocks.')
    pushEvent('action_failed', { action: 'deposit', reason: 'no chest found nearby' })
    return false
  }

  console.log(`[Chest] Walking to chest at ${chestBlock.position}...`)
  try {
    await bot.pathfinder.goto(new goals.GoalGetToBlock(chestBlock.position.x, chestBlock.position.y, chestBlock.position.z))
  } catch (e) {}

  if (bot.entity.position.distanceTo(chestBlock.position) > 4.5) {
    console.log('[Chest] Could not reach chest.')
    return false
  }

  try {
    const chest = await bot.openChest(chestBlock)
    console.log('[Chest] Opened chest!')

    const cropsToDeposit = [
      'wheat', 'carrot', 'potato', 'beetroot',
      'melon_slice', 'pumpkin', 'sweet_berries', 'sugar_cane', 'bread'
    ]

    let depositedAny = false
    for (const item of bot.inventory.items()) {
      if (cropsToDeposit.includes(item.name)) {
        console.log(`[Chest] Depositing ${item.count}x ${item.name}...`)
        await chest.deposit(item.type, null, item.count)
        depositedAny = true
        await new Promise(r => setTimeout(r, 120))
      } else if (['wheat_seeds', 'seeds', 'beetroot_seeds'].includes(item.name)) {
        // Keep at least 16 seeds for future replanting, deposit the rest
        if (item.count > 16) {
          const excess = item.count - 16
          console.log(`[Chest] Depositing excess ${excess}x ${item.name}...`)
          await chest.deposit(item.type, null, excess)
          depositedAny = true
          await new Promise(r => setTimeout(r, 120))
        }
      }
    }

    chest.close()
    console.log('[Chest] Chest closed successfully.')
    pushEvent('deposited_items', { deposited: depositedAny })
    return true
  } catch (err) {
    console.error('[Chest] Error interacting with chest:', err.message)
    return false
  }
}

// ── Companion & Combat Helpers ─────────────────────────────────
async function equipBestWeapon() {
  if (!bot || !bot.inventory) return null
  const priority = [
    'netherite_sword', 'diamond_sword', 'iron_sword', 'stone_sword', 'golden_sword', 'wooden_sword',
    'netherite_axe', 'diamond_axe', 'iron_axe', 'stone_axe', 'golden_axe', 'wooden_axe'
  ]
  const items = bot.inventory.items()
  for (const name of priority) {
    const item = items.find(i => i.name === name)
    if (item) {
      try {
        await bot.equip(item, 'hand')
        return item
      } catch (e) {}
    }
  }
  return null
}

async function equipBestPickaxe() {
  if (!bot || !bot.inventory) return null
  const priority = [
    'netherite_pickaxe', 'diamond_pickaxe', 'iron_pickaxe', 'stone_pickaxe', 'golden_pickaxe', 'wooden_pickaxe'
  ]
  const items = bot.inventory.items()
  for (const name of priority) {
    const item = items.find(i => i.name === name)
    if (item) {
      try {
        await bot.equip(item, 'hand')
        return item
      } catch (e) {}
    }
  }
  return null
}

function isSafeToMine(block, leader) {
  if (!block || !block.position || !bot || !bot.entity) return false
  const botFoot = bot.entity.position.floored().offset(0, -1, 0)
  if (block.position.equals(botFoot)) return false
  if (leader && leader.position) {
    const leaderFoot = leader.position.floored().offset(0, -1, 0)
    if (block.position.equals(leaderFoot)) return false
  }
  return true
}

function hasAirNeighbor(block) {
  if (!block || !block.position || !bot) return false
  const offsets = [
    [0, 1, 0], [0, -1, 0],
    [1, 0, 0], [-1, 0, 0],
    [0, 0, 1], [0, 0, -1]
  ]
  for (const [dx, dy, dz] of offsets) {
    const neighbor = bot.blockAt(block.position.offset(dx, dy, dz))
    if (neighbor && neighbor.name === 'air') return true
  }
  return false
}

// ── Friendly Spam Crouch Helper ────────────────────────────────
let lastFriendlyCrouchTime = 0
let isCrouching = false

async function performFriendlyCrouch(times = 4) {
  if (!bot || !bot.entity || isCrouching) return
  isCrouching = true
  try {
    for (let i = 0; i < times; i++) {
      bot.setControlState('sneak', true)
      await new Promise(r => setTimeout(r, 140))
      bot.setControlState('sneak', false)
      await new Promise(r => setTimeout(r, 140))
    }
  } catch (e) {
  } finally {
    if (bot) bot.setControlState('sneak', false)
    isCrouching = false
  }
}

async function checkFriendlyCrouch() {
  if (!bot || !bot.entity || isCrouching) return
  if (currentAction !== 'idle' || isPerformingAction) return
  if (bot.pathfinder && bot.pathfinder.isMoving()) return

  const now = Date.now()
  if (now - lastFriendlyCrouchTime < 35000) return // 35s cooldown per friendly crouch

  // Look for any nearby player within 3.0 blocks
  const nearbyPlayer = Object.values(bot.entities).find(e => 
    e.type === 'player' && e !== bot.entity && e.position &&
    bot.entity.position.distanceTo(e.position) <= 3.0
  )

  if (!nearbyPlayer || !nearbyPlayer.position) return

  // Check if player is directly in front of Luna (dot > 0.35)
  const toPlayer = nearbyPlayer.position.minus(bot.entity.position).normalize()
  const yaw = bot.entity.yaw
  const facingX = -Math.sin(yaw)
  const facingZ = -Math.cos(yaw)
  const dot = facingX * toPlayer.x + facingZ * toPlayer.z

  if (dot > 0.35) {
    lastFriendlyCrouchTime = now
    console.log(`[Friendly] ${nearbyPlayer.username || 'Player'} is in front of Luna! Doing friendly spam crouch...`)
    try {
      await bot.lookAt(nearbyPlayer.position.offset(0, 1.5, 0))
    } catch (e) {}
    await performFriendlyCrouch(3)
    pushEvent('friendly_crouch', { player: nearbyPlayer.username || 'player' })
  }
}

async function companionTick() {
  if (!bot || !bot.entity || !companionMode.active || companionMode.isBusy) return

  // 0. Water escape reflex
  if (bot.entity.isInWater) {
    companionMode.isBusy = true
    await escapeWater()
    companionMode.isBusy = false
    return
  }

  // Find leader player
  const leaderName = (companionMode.leader || 'kairuuowo').toLowerCase()
  const leader = Object.values(bot.entities).find(e => 
    e.type === 'player' && e.username && (
      e.username.toLowerCase() === leaderName ||
      e.username.toLowerCase().includes(leaderName)
    )
  ) || Object.values(bot.entities).find(e => e.type === 'player' && e !== bot.entity)

  if (!leader || !leader.position) return

  // 1. DEFEND PLAYER & LUNA FROM HOSTILE MOBS
  const hostileMob = Object.values(bot.entities).find(e => 
    e.name && HOSTILE_MOBS.includes(e.name.toLowerCase()) && e.position && (
      bot.entity.position.distanceTo(e.position) <= 12 ||
      leader.position.distanceTo(e.position) <= 14
    )
  )

  if (hostileMob && hostileMob.position) {
    companionMode.isBusy = true
    console.log(`[Companion Combat] Hostile ${hostileMob.name} detected near ${leader.username || 'player'}! Intercepting...`)
    await equipBestWeapon()
    try {
      bot.pathfinder.setGoal(new goals.GoalFollow(hostileMob, 1.8), true)
      for (let i = 0; i < 8; i++) {
        const target = bot.entities[hostileMob.id]
        if (!target || !target.position) break
        const dist = bot.entity.position.distanceTo(target.position)
        if (dist > 16) break
        if (dist <= 3.8) {
          await bot.lookAt(target.position.offset(0, (target.height || 1.6) * 0.75, 0))
          bot.attack(target)
        }
        await new Promise(r => setTimeout(r, 550))
      }
      await collectDropsNearby(5)
    } catch (e) {
      console.log(`[Companion Combat] Error: ${e.message}`)
    }
    companionMode.isBusy = false
    return
  }

  // 2. MINE WITH PLAYER: Check for exposed ores or minable blocks nearby
  const distToLeader = bot.entity.position.distanceTo(leader.position)
  if (distToLeader <= 7.0 && (Date.now() - companionMode.lastMineTime > 2000)) {
    // Look for valuable ores within 5.5 blocks of Luna
    const oreBlock = bot.findBlock({
      matching: (b) => ORE_BLOCKS.includes(b.name),
      maxDistance: 5.5
    })

    if (oreBlock && isSafeToMine(oreBlock, leader)) {
      companionMode.isBusy = true
      companionMode.lastMineTime = Date.now()
      console.log(`[Companion Mining] Found ${oreBlock.name} at ${oreBlock.position}! Digging...`)
      await equipBestPickaxe()
      try {
        const d = bot.entity.position.distanceTo(oreBlock.position)
        if (d > 3.8) {
          await bot.pathfinder.goto(new goals.GoalLookAtBlock(oreBlock.position, bot.world))
        }
        if (bot.canDigBlock(oreBlock)) {
          await bot.lookAt(oreBlock.position.offset(0.5, 0.5, 0.5))
          await bot.dig(oreBlock)
          pushEvent('mined_ore', { ore: oreBlock.name })
          await collectDropsNearby(4)
        }
      } catch (e) {
        console.log(`[Companion Mining] Dig error: ${e.message}`)
      }
      companionMode.isBusy = false
      return
    }

    // If leader is holding a pickaxe and near stone/cobblestone/deepslate, help mine!
    const leaderItem = leader.heldItem?.name || ''
    if (leaderItem.includes('pickaxe')) {
      const stoneBlock = bot.findBlock({
        matching: (b) => MINABLE_STONE_BLOCKS.includes(b.name) && hasAirNeighbor(b),
        maxDistance: 4.0
      })
      if (stoneBlock && isSafeToMine(stoneBlock, leader)) {
        companionMode.isBusy = true
        companionMode.lastMineTime = Date.now()
        console.log(`[Companion Mining] Assisting with ${stoneBlock.name} at ${stoneBlock.position}...`)
        await equipBestPickaxe()
        try {
          if (bot.canDigBlock(stoneBlock)) {
            await bot.lookAt(stoneBlock.position.offset(0.5, 0.5, 0.5))
            await bot.dig(stoneBlock)
            await collectDropsNearby(4)
          }
        } catch (e) {}
        companionMode.isBusy = false
        return
      }
    }
  }

  // 3. TORCH PLACEMENT in dark areas
  const currentBlock = bot.blockAt(bot.entity.position)
  if (currentBlock && currentBlock.light < 5 && (Date.now() - companionMode.lastTorchTime > 15000)) {
    const torchItem = bot.inventory.items().find(i => i.name === 'torch' || i.name === 'soul_torch')
    if (torchItem) {
      const ground = bot.blockAt(bot.entity.position.offset(0, -1, 0))
      if (ground && ground.name !== 'air' && ground.name !== 'water' && ground.name !== 'torch') {
        try {
          await bot.equip(torchItem, 'hand')
          await bot.placeBlock(ground, new Vec3(0, 1, 0))
          companionMode.lastTorchTime = Date.now()
          console.log('[Companion] Placed a torch in the dark area.')
        } catch (e) {}
      }
    }
  }

  // 4. COLLECT NEARBY DROPS
  if (Date.now() - companionMode.lastDropCollectTime > 3000) {
    companionMode.lastDropCollectTime = Date.now()
    const nearbyDrop = Object.values(bot.entities).find(e => 
      (e.name === 'item' || e.type === 'object') && 
      bot.entity.position.distanceTo(e.position) <= 4.0
    )
    if (nearbyDrop) {
      await collectDropsNearby(4)
    }
  }

  // 5. FOLLOW LEADER CLOSELY
  if (distToLeader > 3.2) {
    try {
      bot.pathfinder.setGoal(new goals.GoalFollow(leader, 2.5), true)
    } catch (e) {}
  } else if (distToLeader <= 2.2) {
    if (bot.pathfinder.isMoving()) {
      bot.pathfinder.setGoal(null)
    }
    try {
      await bot.lookAt(leader.position.offset(0, 1.6, 0))
    } catch (e) {}
  }
}

createBot()

// ── Action Implementation Logic ───────────────────────────────
async function performAction(action, params = {}) {
  console.log(`[Executing Action] ${action}`, params)
  
  // Cancel any active sneak/crouch lock
  isCrouching = false
  if (bot) bot.setControlState('sneak', false)

  const m = getMovements()
  if (m && bot && bot.pathfinder) bot.pathfinder.setMovements(m)

  currentAction = action
  isPerformingAction = (action !== 'idle' && action !== 'look_around')

  try {
    switch (action) {
      case 'chat': {
        if (params.message && bot && bot.entity) {
          try { bot.chat(params.message) } catch (e) {}
        }
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'farm':
      case 'harvest': {
        console.log('[Action] Rapid Farming: Scanning field for mature crops...')
        
        let totalHarvested = 0
        const maxPerRun = 45

        for (let iteration = 0; iteration < maxPerRun; iteration++) {
          if (!bot || !bot.entity || !bot.entity.position) break

          // 1. Immediately harvest & replant ALL crops within reaching range (<= 4.2m)
          const reachableCrops = bot.findBlocks({
            matching: isMatureCrop,
            maxDistance: 4.2,
            count: 16
          }).map(pos => bot.blockAt(pos)).filter(b => b && b.position)

          if (reachableCrops.length > 0) {
            for (const crop of reachableCrops) {
              try {
                const ok = await harvestAndReplant(crop)
                if (ok) totalHarvested++
              } catch (e) {}
            }
          }

          // 2. Scan if any other mature crops exist on the entire farm (<= 24m)
          const remainingCrops = bot.findBlocks({
            matching: isMatureCrop,
            maxDistance: 24,
            count: 24
          }).map(pos => bot.blockAt(pos)).filter(b => b && b.position)

          if (remainingCrops.length === 0) {
            // Whole field is cleared!
            break
          }

          // Sort by nearest to bot
          remainingCrops.sort((a, b) => bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position))
          const nextTarget = remainingCrops[0]
          if (!nextTarget || !nextTarget.position) break

          // Walk to the next cluster (stand 2 blocks away to reach without trampling)
          try {
            await bot.pathfinder.goto(new goals.GoalNearXZ(nextTarget.position.x, nextTarget.position.z, 2))
          } catch (e) {
            break // If path blocked, finish run
          }
        }

        if (totalHarvested > 0) {
          console.log(`[Farm] Fast harvest completed! Harvested & replanted ${totalHarvested} crops.`)
          pushEvent('harvested_crop', { count: totalHarvested })
          // Quick vacuum of dropped items
          await collectDropsNearby(8)
        } else {
          // Check for empty farmland + seeds in inventory to plant
          const farmlandPositions = bot.findBlocks({
            matching: (b) => b && b.name === 'farmland',
            maxDistance: 16,
            count: 12
          })
          const emptyFarmlands = farmlandPositions
            .map(pos => bot.blockAt(pos))
            .filter(b => {
              if (!b || !b.position) return false
              const above = bot.blockAt(new Vec3(b.position.x, b.position.y + 1, b.position.z))
              return above && above.name === 'air'
            })

          const seedItem = bot.inventory.items().find(i => 
            ['wheat_seeds', 'seeds', 'carrot', 'potato', 'beetroot_seeds'].includes(i.name)
          )

          if (emptyFarmlands.length > 0 && seedItem) {
            for (const farmland of emptyFarmlands.slice(0, 6)) {
              if (!farmland || !farmland.position) continue
              try {
                if (bot.entity.position.distanceTo(farmland.position) > 4.2) {
                  await bot.pathfinder.goto(new goals.GoalNearXZ(farmland.position.x, farmland.position.z, 2))
                }
                await bot.equip(seedItem, 'hand')
                await bot.placeBlock(farmland, new Vec3(0, 1, 0))
                pushEvent('replanted_crop', { seed: seedItem.name })
              } catch (e) {}
            }
          } else {
            console.log('[Farm] No mature crops or plantable farmland found nearby')
            pushEvent('action_failed', { action: 'farm', reason: 'no crops ready to harvest' })
            const angle = Math.random() * Math.PI * 2
            const targetX = bot.entity.position.x + Math.cos(angle) * 16
            const targetZ = bot.entity.position.z + Math.sin(angle) * 16
            bot.pathfinder.setGoal(new goals.GoalNearXZ(targetX, targetZ, 2))
          }
        }

        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'eat': {
        await autoEat()
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'deposit':
      case 'store': {
        await depositInChest()
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'drop':
      case 'give':
      case 'toss': {
        const playerName = (params.player || 'kairuuowo').toLowerCase()
        const player = Object.values(bot.entities).find(e => e.type === 'player' && e.username && e.username.toLowerCase().includes(playerName))
        if (player && player.position) {
          try { await bot.lookAt(new Vec3(player.position.x, player.position.y + 1.4, player.position.z)) } catch (e) {}
        }
        const requested = (params.item || 'all').toLowerCase()
        let tossed = 0
        for (const item of bot.inventory.items()) {
          if (requested === 'all' || requested === 'everything' || item.name.toLowerCase().includes(requested)) {
            try {
              await bot.tossStack(item)
              tossed++
              await new Promise(r => setTimeout(r, 120))
            } catch (e) {}
          }
        }
        if (tossed > 0) {
          console.log(`[Toss] Tossed ${tossed} item stacks to ${playerName}`)
          pushEvent('tossed_items', { count: tossed })
        }
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'companion_mode':
      case 'explore_mode':
      case 'explore_with_me': {
        const playerName = (params.player || 'kairuuowo').toLowerCase()
        const player = Object.values(bot.entities).find(e => e.type === 'player' && e.username && e.username.toLowerCase().includes(playerName)) || Object.values(bot.entities).find(e => e.type === 'player' && e !== bot.entity)
        companionMode.active = true
        companionMode.leader = player ? player.username.toLowerCase() : playerName
        companionMode.isBusy = false
        currentAction = 'companion'
        isPerformingAction = true

        const m = getMovements()
        if (m && bot && bot.pathfinder) bot.pathfinder.setMovements(m)
        await equipBestWeapon()

        if (player) {
          bot.pathfinder.setGoal(new goals.GoalFollow(player, 2.5), true)
        }
        console.log(`[Companion] Explore mode ACTIVATED for ${companionMode.leader}! Following, defending, and mining.`)
        pushEvent('companion_mode_started', { leader: companionMode.leader })
        try {
          bot.chat("explore mode on! i'll follow you, defend you from mobs, and mine with you!")
        } catch (e) {}
        break
      }

      case 'stop_companion': {
        companionMode.active = false
        companionMode.isBusy = false
        bot.pathfinder.setGoal(null)
        currentAction = 'idle'
        isPerformingAction = false
        console.log('[Companion] Explore mode STOPPED.')
        pushEvent('companion_mode_stopped', {})
        try {
          bot.chat("explore mode deactivated! staying here.")
        } catch (e) {}
        break
      }

      case 'guard':
      case 'protect':
      case 'defend': {
        const playerName = (params.player || 'kairuuowo').toLowerCase()
        const player = Object.values(bot.entities).find(e => e.type === 'player' && e.username && e.username.toLowerCase().includes(playerName)) || Object.values(bot.entities).find(e => e.type === 'player' && e !== bot.entity)

        if (player) {
          console.log(`[Guard] Guarding ${player.username}...`)
          companionMode.active = true
          companionMode.leader = player.username.toLowerCase()
          companionMode.isBusy = false
          await equipBestWeapon()
          bot.pathfinder.setGoal(new goals.GoalFollow(player, 2.5), true)
          currentAction = 'companion'
          isPerformingAction = true
          pushEvent('guarding_player', { player: player.username })
        } else {
          isPerformingAction = false
          currentAction = 'idle'
        }
        break
      }

      case 'feed': {
        const playerName = (params.player || 'kairuuowo').toLowerCase()
        const player = Object.values(bot.entities).find(e => e.type === 'player' && e.username && e.username.toLowerCase().includes(playerName))
        const edible = ['bread', 'cooked_beef', 'steak', 'cooked_porkchop', 'cooked_mutton', 'cooked_chicken', 'apple', 'baked_potato', 'carrot', 'sweet_berries']
        const food = bot.inventory.items().find(i => edible.includes(i.name))
        if (food && player && player.position) {
          try {
            await bot.lookAt(new Vec3(player.position.x, player.position.y + 1.4, player.position.z))
            await bot.toss(food.type, null, Math.min(food.count, 4))
            console.log(`[Feed] Gave ${food.name} to ${player.username}`)
            pushEvent('fed_player', { food: food.name })
          } catch (e) {}
        } else {
          try { bot.chat("i don't have any food on me!") } catch (e) {}
        }
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'equip': {
        const targetName = (params.item || 'sword').toLowerCase()
        const item = bot.inventory.items().find(i => i.name.toLowerCase().includes(targetName))
        if (item) {
          const dest = item.name.includes('helmet') ? 'head' : (item.name.includes('chestplate') ? 'torso' : (item.name.includes('leggings') ? 'legs' : (item.name.includes('boots') ? 'feet' : 'hand')))
          await bot.equip(item, dest).catch(() => {})
          console.log(`[Equip] Equipped ${item.name} to ${dest}`)
          pushEvent('equipped_item', { item: item.name })
        }
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'inventory': {
        const items = bot.inventory.items().map(i => `${i.count}x ${i.name}`).join(', ')
        const msg = items ? `i'm holding: ${items}` : "my inventory is completely empty!"
        try { bot.chat(msg) } catch (e) {}
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'torch':
      case 'light': {
        const torch = bot.inventory.items().find(i => i.name.includes('torch'))
        const ground = bot.blockAt(new Vec3(bot.entity.position.x, bot.entity.position.y - 1, bot.entity.position.z))
        if (torch && ground) {
          try {
            await bot.equip(torch, 'hand')
            await bot.placeBlock(ground, new Vec3(0, 1, 0))
            console.log('[Torch] Placed torch!')
            pushEvent('placed_torch', {})
          } catch (e) {}
        } else if (!torch) {
          try { bot.chat("i don't have any torches!") } catch (e) {}
        }
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'sleep': {
        const bed = bot.findBlock({
          matching: (b) => b.name.includes('bed'),
          maxDistance: 16
        })
        if (bed) {
          try {
            await bot.pathfinder.goto(new goals.GoalGetToBlock(bed.position.x, bed.position.y, bed.position.z))
            await bot.sleep(bed)
            pushEvent('sleeping', {})
          } catch (e) {
            pushEvent('action_failed', { action: 'sleep', reason: e.message })
          }
        } else {
          try { bot.chat("can't find a bed nearby!") } catch (e) {}
        }
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'follow':
      case 'follow_player': {
        const playerName = (params.player || 'kairuuowo').toLowerCase()
        const target = Object.values(bot.entities).find(e => 
          e.type === 'player' && e.username && (
            e.username.toLowerCase() === playerName ||
            e.username.toLowerCase().includes(playerName)
          )
        ) || Object.values(bot.entities).find(e => e.type === 'player' && e !== bot.entity)

        if (target) {
          console.log(`[Pathfinder] Following player: ${target.username}`)
          companionMode.active = true
          companionMode.leader = target.username.toLowerCase()
          companionMode.isBusy = false
          currentAction = 'companion'
          isPerformingAction = true
          await equipBestWeapon()
          bot.pathfinder.setGoal(new goals.GoalFollow(target, 2.5), true)
          pushEvent('following', { target: target.username })
        } else {
          console.log(`[Pathfinder] Player ${playerName} not found nearby, exploring area instead`)
          const angle = Math.random() * Math.PI * 2
          const targetX = bot.entity.position.x + Math.cos(angle) * 16
          const targetZ = bot.entity.position.z + Math.sin(angle) * 16
          bot.pathfinder.setGoal(new goals.GoalNearXZ(targetX, targetZ, 2))
          pushEvent('exploring', { target: { x: targetX, z: targetZ } })
        }
        break
      }

      case 'collect':
      case 'gather': {
        // 1. First check for dropped items on the ground within 16 blocks
        const droppedItem = Object.values(bot.entities).find(e => 
          (e.name === 'item' || e.type === 'object') && 
          bot.entity.position.distanceTo(e.position) < 16
        )

        if (droppedItem) {
          console.log(`[Pathfinder] Walking to collect dropped item at ${droppedItem.position}`)
          bot.pathfinder.setGoal(new goals.GoalNearXZ(droppedItem.position.x, droppedItem.position.z, 1))
          pushEvent('item_collected', { item: droppedItem.displayName || 'item' })
          break
        }

        // 2. Otherwise harvest nearby harvestable block
        const blockName = params.block || 'log'
        const block = bot.findBlock({
          matching: (b) => b.name.includes(blockName) || b.name.includes('wood') || b.name.includes('dirt') || b.name.includes('stone'),
          maxDistance: 16
        })

        if (block) {
          console.log(`[Pathfinder] Walking to block ${block.name} at ${block.position}`)
          bot.pathfinder.setGoal(new goals.GoalGetToBlock(block.position.x, block.position.y, block.position.z))
          pushEvent('collecting', { block: block.name })
        } else {
          // Wander to find resources
          const angle = Math.random() * Math.PI * 2
          const targetX = bot.entity.position.x + Math.cos(angle) * 16
          const targetZ = bot.entity.position.z + Math.sin(angle) * 16
          bot.pathfinder.setGoal(new goals.GoalNearXZ(targetX, targetZ, 2))
        }
        break
      }

      case 'status': {
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'look_at': {
        if (params.target) {
          const player = bot.players[params.target]?.entity
          if (player && player.position) {
            await bot.lookAt(new Vec3(player.position.x, player.position.y + 1.6, player.position.z))
          }
        }
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'explore': {
        // If in water, find nearest dry land to escape
        if (bot.entity && bot.entity.isInWater) {
          const land = bot.findBlock({
            matching: (b) => b.name !== 'water' && b.name !== 'flowing_water' && b.name !== 'air',
            maxDistance: 16
          })
          if (land) {
            console.log(`[Pathfinder] Swimming out of water towards dry land at ${land.position}`)
            bot.pathfinder.setGoal(new goals.GoalNearXZ(land.position.x, land.position.z, 1))
            pushEvent('exploring', { target: land.position })
            break
          }
        }

        // Wander 12 to 24 blocks away using 2D horizontal goal
        const angle = Math.random() * Math.PI * 2
        const dist = 12 + Math.random() * 12
        const targetX = bot.entity.position.x + Math.cos(angle) * dist
        const targetZ = bot.entity.position.z + Math.sin(angle) * dist
        console.log(`[Pathfinder] Exploring towards: ${targetX.toFixed(1)}, ${targetZ.toFixed(1)}`)
        bot.pathfinder.setGoal(new goals.GoalNearXZ(targetX, targetZ, 2))
        pushEvent('exploring', { target: { x: targetX, z: targetZ } })
        break
      }

      case 'flee': {
        const hostiles = ['creeper', 'zombie', 'skeleton', 'spider', 'enderman', 'witch', 'blaze']
        const entities = Object.values(bot.entities)
          .filter(e => e.type === 'mob' && hostiles.includes(e.name) && bot.entity.position.distanceTo(e.position) < 16)
        
        if (entities.length > 0) {
          const danger = entities[0]
          const diff = bot.entity.position.minus(danger.position).normalize()
          const escapePos = bot.entity.position.plus(diff.scaled(20))
          
          bot.pathfinder.setGoal(new goals.GoalNearXZ(escapePos.x, escapePos.z, 2))
          pushEvent('fleeing', { from: danger.name || 'mob' })
        } else {
          // No hostiles directly threatening - sprint away to safety and eat
          const angle = Math.random() * Math.PI * 2
          const targetX = bot.entity.position.x + Math.cos(angle) * 20
          const targetZ = bot.entity.position.z + Math.sin(angle) * 20
          bot.pathfinder.setGoal(new goals.GoalNearXZ(targetX, targetZ, 2))
          pushEvent('fleeing', { from: 'danger' })
        }
        await autoEat()
        break
      }

      case 'attack':
      case 'kill':
      case 'fight':
      case 'hit': {
        const targetQuery = (params.target || params.player || params.mob || '').toLowerCase().trim()
        const leaderName = (companionMode.leader || 'kairuuowo').toLowerCase()

        let target = null

        // 1. If target name was given:
        if (targetQuery && targetQuery !== 'someone' && targetQuery !== 'target') {
          // Check player first (e.g. Wenphix)
          target = Object.values(bot.entities).find(e => 
            e.type === 'player' && e !== bot.entity && e.username &&
            e.username.toLowerCase().includes(targetQuery)
          )

          // Check mob by name if no player matches
          if (!target) {
            target = Object.values(bot.entities).find(e => 
              (e.type === 'mob' || e.type === 'animal') && e.name && 
              e.name.toLowerCase().includes(targetQuery) &&
              e !== bot.entity && e.position &&
              bot.entity.position.distanceTo(e.position) <= 24
            )
          }
        }

        // 2. If no target specified or not found, find nearest hostile mob
        if (!target) {
          const hostiles = Object.values(bot.entities)
            .filter(e => e.name && HOSTILE_MOBS.includes(e.name.toLowerCase()) && e.position)
            .sort((a, b) => bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position))
          target = hostiles[0]
        }

        // 3. Fallback: nearest non-leader player within 16 blocks if target requested was a generic player
        if (!target && targetQuery) {
          target = Object.values(bot.entities).find(e => 
            e.type === 'player' && e !== bot.entity && e.username &&
            !e.username.toLowerCase().includes(leaderName) &&
            bot.entity.position.distanceTo(e.position) < 16
          )
        }

        if (target) {
          const tName = target.username || target.name || 'target'
          console.log(`[Attack Command] Attacking ${tName}!`)
          try { bot.chat(`on it, attacking ${tName}!`) } catch (e) {}
          await equipBestWeapon()
          pushEvent('attacking', { target: tName })
          await retaliate(target)
        } else {
          console.log(`[Attack Command] Target '${targetQuery || 'enemy'}' not found nearby.`)
          try { bot.chat(`can't find ${targetQuery || 'anyone to attack'} nearby!`) } catch (e) {}
          isPerformingAction = false
          currentAction = 'idle'
        }
        break
      }

      case 'crouch':
      case 'sneak':
      case 'friendly_crouch': {
        const leader = Object.values(bot.entities).find(e => e.type === 'player' && e !== bot.entity)
        if (leader && leader.position) {
          try { await bot.lookAt(leader.position.offset(0, 1.5, 0)) } catch (e) {}
        }
        console.log('[Friendly Crouch] Performing spam crouch greeting!')
        await performFriendlyCrouch(4)
        pushEvent('crouched', {})
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'mine': {
        const block = bot.findBlock({
          matching: (b) => ['stone', 'coal_ore', 'iron_ore', 'dirt'].includes(b.name),
          maxDistance: 8
        })

        if (block) {
          bot.pathfinder.setGoal(new goals.GoalGetToBlock(block.position.x, block.position.y, block.position.z))
          pushEvent('mined_block', { block: block.name })
        } else {
          // If no mine block right here, explore to find one
          const angle = Math.random() * Math.PI * 2
          const targetX = bot.entity.position.x + Math.cos(angle) * 15
          const targetZ = bot.entity.position.z + Math.sin(angle) * 15
          bot.pathfinder.setGoal(new goals.GoalNearXZ(targetX, targetZ, 2))
        }
        break
      }

      case 'look_around': {
        const yaw = (Math.random() - 0.5) * 2 * Math.PI
        const pitch = (Math.random() - 0.5) * Math.PI * 0.2
        await bot.look(yaw, pitch, true)
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      case 'idle': {
        companionMode.active = false
        companionMode.isBusy = false
        bot.pathfinder.setGoal(null)
        pushEvent('idle', {})
        isPerformingAction = false
        currentAction = 'idle'
        break
      }

      default:
        console.log(`Unknown action requested: ${action}`)
        isPerformingAction = false
        currentAction = 'idle'
        break
    }
  } catch (err) {
    console.error(`Action ${action} failed:`, err.stack || err.message)
    pushEvent('action_failed', { action, reason: err.message })
    isPerformingAction = false
    currentAction = 'idle'
  }

  return { ok: true }
}

// ── HTTP Bridge ───────────────────────────────────────────────
app.get('/events', (req, res) => {
  const events = [...eventQueue]
  eventQueue = []
  res.json({ events })
})

app.get('/state', (req, res) => {
  if (!bot || !bot.entity || !bot.entity.position) return res.json({ connected: false })

  const hostilesList = ['creeper', 'zombie', 'skeleton', 'spider', 'enderman', 'witch', 'blaze', 'slime', 'drowned']

  const allNearby = Object.values(bot.entities)
    .filter(e => e !== bot.entity && e.position && bot.entity.position.distanceTo(e.position) < 24)

  const players = allNearby
    .filter(e => e.type === 'player' && e.username)
    .map(e => ({ name: e.username, dist: Math.round(bot.entity.position.distanceTo(e.position)) }))

  const hostiles = allNearby
    .filter(e => e.type === 'mob' && hostilesList.includes(e.name))
    .map(e => ({ name: e.name, dist: Math.round(bot.entity.position.distanceTo(e.position)) }))

  const items = allNearby
    .filter(e => e.name === 'item' || e.type === 'object')
    .map(e => ({ name: e.displayName || 'item', dist: Math.round(bot.entity.position.distanceTo(e.position)) }))

  const matureCrop = bot.findBlock({
    matching: isMatureCrop,
    maxDistance: 16
  })

  const isMoving = Boolean(bot.pathfinder && bot.pathfinder.isMoving())

  res.json({
    connected: true,
    health: Math.round(bot.health),
    food: Math.round(bot.food),
    position: bot.entity.position,
    time: bot.time.timeOfDay > 13000 ? 'night' : 'day',
    nearby_players: players,
    hostile_mobs: hostiles,
    nearby_items: items,
    mature_crops: matureCrop ? matureCrop.name : null,
    current_action: currentAction,
    is_performing_action: isPerformingAction,
    is_moving: isMoving,
    companion_mode: companionMode.active,
    companion_leader: companionMode.leader
  })
})

app.post('/action', async (req, res) => {
  const { action, params } = req.body
  const result = await performAction(action, params)
  res.json(result)
})

// ── Autonomous Auto-Farm & Auto-Deposit Loop ───────────────────
let isAutoFarming = false

async function autoFarmTick() {
  if (!bot || !bot.entity || !bot.entity.position || isPerformingAction || currentAction !== 'idle' || isAutoFarming || companionMode.active) return

  // 0. If fallen into irrigation water hole, climb out immediately!
  if (bot.entity.isInWater) {
    isAutoFarming = true
    await escapeWater()
    isAutoFarming = false
    return
  }

  // 1. Check if there are mature crops nearby
  const cropBlock = bot.findBlock({
    matching: isMatureCrop,
    maxDistance: 20
  })

  if (cropBlock) {
    isAutoFarming = true
    console.log(`[Auto-Farm] Detected mature ${cropBlock.name}, harvesting...`)
    try {
      await performAction('farm')
    } catch (e) {}
    isAutoFarming = false
    return
  }

  // 2. Check if we have harvested crops in inventory and a chest nearby
  const cropsToStore = ['wheat', 'carrot', 'potato', 'beetroot', 'melon_slice', 'pumpkin', 'sweet_berries', 'sugar_cane']
  const hasHarvestedCrops = bot.inventory.items().some(i => cropsToStore.includes(i.name))
  const seedsItem = bot.inventory.items().find(i => ['wheat_seeds', 'seeds'].includes(i.name))
  const hasExcessSeeds = Boolean(seedsItem && seedsItem.count > 16)

  if (hasHarvestedCrops || hasExcessSeeds) {
    const chestBlock = bot.findBlock({
      matching: (b) => b.name === 'chest' || b.name === 'trapped_chest' || b.name === 'barrel',
      maxDistance: 20
    })

    if (chestBlock) {
      isAutoFarming = true
      console.log('[Auto-Farm] Field clear & crops in inventory. Automatically depositing in chest...')
      try {
        await performAction('deposit')
      } catch (e) {}
      isAutoFarming = false
    }
  }
}

// Check for mature crops and chest deposits every 1.5 seconds
setInterval(autoFarmTick, 1500)
// Run companion follow, defense, mining, and lighting loop every 1.0 second
setInterval(companionTick, 1000)
// Autonomous friendly spam crouch when someone stands in front of Luna
setInterval(checkFriendlyCrouch, 800)
// ── Anti-Stuck Watchdog Loop ──────────────────────────────────
let lastStuckPos = null
let stuckTicks = 0
let isUnsticking = false

async function antiStuckWatchdog() {
  if (!bot || !bot.entity || !bot.entity.position || isUnsticking) return

  const isMoving = Boolean(bot.pathfinder && bot.pathfinder.isMoving())
  const hasActiveAction = currentAction !== 'idle' && currentAction !== 'sleeping'

  if (!isMoving && !hasActiveAction) {
    stuckTicks = 0
    lastStuckPos = null
    return
  }

  const currentPos = bot.entity.position.clone()
  if (lastStuckPos) {
    const distMoved = currentPos.distanceTo(lastStuckPos)
    // If trying to move but moved less than 0.35m in ~1.2s:
    if (distMoved < 0.35) {
      stuckTicks++
      if (stuckTicks >= 2) {
        isUnsticking = true
        console.log(`[Anti-Stuck] Luna is stuck on a block at ${currentPos.floored()}! Performing unstick...`)
        
        if (bot.entity.isInWater) {
          await escapeWater()
        } else {
          // 1. Release forward / sprint
          bot.setControlState('forward', false)
          bot.setControlState('sprint', false)

          // 2. Hop backwards
          bot.setControlState('back', true)
          bot.setControlState('jump', true)
          await new Promise(r => setTimeout(r, 350))
          bot.setControlState('back', false)

          // 3. Sidestep randomly to break collision angle
          const sidestep = Math.random() < 0.5 ? 'left' : 'right'
          bot.setControlState(sidestep, true)
          await new Promise(r => setTimeout(r, 300))
          bot.setControlState(sidestep, false)
          bot.setControlState('jump', false)

          // 4. Force pathfinder to recalculate clean path
          if (bot.pathfinder) {
            const currentGoal = bot.pathfinder.goal
            bot.pathfinder.stop()
            if (currentGoal) {
              await new Promise(r => setTimeout(r, 150))
              bot.pathfinder.setGoal(currentGoal, true)
            }
          }
        }

        stuckTicks = 0
        isUnsticking = false
      }
    } else {
      stuckTicks = 0
    }
  }

  lastStuckPos = currentPos
}

// Watch for stuck bot every 1.2 seconds
setInterval(antiStuckWatchdog, 1200)

app.listen(BRIDGE_PORT, () => {
  console.log(`Mineflayer Express Bridge running on port ${BRIDGE_PORT}`)
})