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

// ── Event helpers ─────────────────────────────────────────────
function pushEvent(type, data = {}) {
  eventQueue.push({ type, data, timestamp: Date.now() })
  console.log(`[event] ${type}`, data)
}

function getMovements() {
  if (!bot) return null
  const mcData = require('minecraft-data')(bot.version)
  const m = new Movements(bot, mcData)
  m.canDig = false
  m.allowParkour = false // Disable jumping across blocks to prevent trampling farmland
  m.allowSprinting = false // Calm walking, prevents trampling crops
  m.canSwim = true
  m.liquidCost = 300 // Strictly avoid walking into irrigation water holes
  m.dontCreateJumpingPath = true // Never generate paths that require jumping

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

  // Auto-swim: hold jump ONLY while in water, and release immediately on land!
  bot.on('physicTick', () => {
    if (bot.entity && bot.entity.isInWater) {
      bot.setControlState('jump', true)
    } else {
      bot.setControlState('jump', false)
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

      // Identify attacker within 4.5m
      const nearby = Object.values(bot.entities)
        .filter(e => e !== bot.entity && (e.type === 'player' || e.type === 'mob') && e.position)
        .sort((a, b) => bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position))

      const attacker = nearby[0]
      if (attacker && bot.entity.position.distanceTo(attacker.position) < 4.5) {
        const attackerName = attacker.username || attacker.name || 'someone'
        console.log(`[Combat] Under attack by ${attackerName}!`)
        pushEvent('attacked_by', { attacker: attackerName })
        await retaliate(attacker)
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
  if (name === 'melon' || name === 'pumpkin') return true
  if (name === 'sugar_cane') {
    const below = bot.blockAt(block.position.offset(0, -1, 0))
    if (below && below.name === 'sugar_cane') return true
  }
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
  if (!cropBlock || !isMatureCrop(cropBlock)) return false
  const cropName = cropBlock.name
  const farmlandPos = cropBlock.position.offset(0, -1, 0)
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

createBot()

// ── Action Implementation Logic ───────────────────────────────
async function performAction(action, params = {}) {
  console.log(`[Executing Action] ${action}`, params)
  
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
          // 1. Immediately harvest & replant ALL crops within reaching range (<= 4.2m)
          const reachableCrops = bot.findBlocks({
            matching: isMatureCrop,
            maxDistance: 4.2,
            count: 16
          }).map(pos => bot.blockAt(pos)).filter(Boolean)

          if (reachableCrops.length > 0) {
            for (const crop of reachableCrops) {
              const ok = await harvestAndReplant(crop)
              if (ok) totalHarvested++
            }
          }

          // 2. Scan if any other mature crops exist on the entire farm (<= 24m)
          const remainingCrops = bot.findBlocks({
            matching: isMatureCrop,
            maxDistance: 24,
            count: 24
          }).map(pos => bot.blockAt(pos)).filter(Boolean)

          if (remainingCrops.length === 0) {
            // Whole field is cleared!
            break
          }

          // Sort by nearest to bot
          remainingCrops.sort((a, b) => bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position))
          const nextTarget = remainingCrops[0]

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
          const emptyFarmlands = bot.findBlocks({
            matching: (b) => {
              if (b.name !== 'farmland') return false
              const above = bot.blockAt(b.position.offset(0, 1, 0))
              return above && above.name === 'air'
            },
            maxDistance: 16,
            count: 8
          }).map(pos => bot.blockAt(pos)).filter(Boolean)

          const seedItem = bot.inventory.items().find(i => 
            ['wheat_seeds', 'seeds', 'carrot', 'potato', 'beetroot_seeds'].includes(i.name)
          )

          if (emptyFarmlands.length > 0 && seedItem) {
            for (const farmland of emptyFarmlands.slice(0, 6)) {
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
          bot.pathfinder.setGoal(new goals.GoalFollow(target, 2), true)
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
          if (player) {
            await bot.lookAt(player.position.offset(0, 1.6, 0))
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

      case 'attack': {
        const targetMob = Object.values(bot.entities)
          .find(e => e.type === 'mob' && bot.entity.position.distanceTo(e.position) < 12)

        if (targetMob) {
          bot.pathfinder.setGoal(new goals.GoalFollow(targetMob, 2))
          
          const attackInterval = setInterval(() => {
            if (currentAction !== 'attack' || !bot.entities[targetMob.id]) {
              clearInterval(attackInterval)
              return
            }
            if (bot.entity.position.distanceTo(targetMob.position) < 3.5) {
              bot.attack(targetMob)
            }
          }, 500)
          
          pushEvent('attacking', { mob: targetMob.name })
        } else {
          isPerformingAction = false
          currentAction = 'idle'
        }
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
    console.error(`Action ${action} failed:`, err.message)
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
    is_moving: isMoving
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
  if (!bot || !bot.entity || isPerformingAction || currentAction !== 'idle' || isAutoFarming) return

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

app.listen(BRIDGE_PORT, () => {
  console.log(`Mineflayer Express Bridge running on port ${BRIDGE_PORT}`)
})