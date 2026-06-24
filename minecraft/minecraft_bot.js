const mineflayer = require('mineflayer')
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder')
const http = require('http')
const express = require('express')

const app = express()
app.use(express.json())

// ── Config ────────────────────────────────────────────────────
const BOT_CONFIG = {
  host: 'ibex.exaroton.host',
  port: 61024,
  username: 'Luna',
  version: '1.20.1',
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

// ── Bot setup ─────────────────────────────────────────────────
function createBot() {
  bot = mineflayer.createBot(BOT_CONFIG)
  bot.loadPlugin(pathfinder)

  bot.once('spawn', () => {
    console.log('Bot spawned in world')
    pushEvent('spawned', { position: bot.entity.position })

    const mcData = require('minecraft-data')(bot.version)
    const defaultMovements = new Movements(bot, mcData)
    bot.pathfinder.setMovements(defaultMovements)
  })

  bot.on('goal_reached', () => {
    console.log('[Pathfinder] Reached destination.')
    isPerformingAction = false
    currentAction = 'idle'
    pushEvent('arrived', {})
  })

  bot.on('entityHurt', (entity) => {
    if (entity === bot.entity) {
      pushEvent('took_damage', { health: Math.round(bot.health) })
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
    pushEvent('died', {})
    isPerformingAction = false
    currentAction = 'idle'
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
  })

  bot.on('kicked', (reason) => {
    console.log(`Kicked from server: ${reason}`)
    pushEvent('disconnected', { reason })
  })

  bot.on('error', (err) => {
    console.error('Mineflayer Error:', err)
  })
}

createBot()

// ── Action Implementation Logic ───────────────────────────────
async function performAction(action, params = {}) {
  console.log(`[Executing Action] ${action}`, params)
  
  const mcData = require('minecraft-data')(bot.version)
  const defaultMovements = new Movements(bot, mcData)
  bot.pathfinder.setMovements(defaultMovements)

  currentAction = action
  isPerformingAction = (action !== 'idle' && action !== 'look_around')

  try {
    switch (action) {
      case 'chat': {
        if (params.message) bot.chat(params.message)
        // Chat finishes instantly
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
        // Pick a random direction and walk 12-20 blocks away
        const rx = (Math.random() - 0.5) * 30
        const rz = (Math.random() - 0.5) * 30
        const targetPos = bot.entity.position.offset(rx, 0, rz)
        
        bot.pathfinder.setGoal(new goals.GoalXZ(targetPos.x, targetPos.z))
        pushEvent('exploring', { target: targetPos })
        break
      }

      case 'flee': {
        // Run in opposite direction of nearest entity
        const entities = Object.values(bot.entities)
          .filter(e => e.type === 'mob' && bot.entity.position.distanceTo(e.position) < 16)
        
        if (entities.length > 0) {
          const danger = entities[0]
          const diff = bot.entity.position.minus(danger.position).normalize()
          const escapePos = bot.entity.position.plus(diff.scaled(15))
          
          bot.pathfinder.setGoal(new goals.GoalXZ(escapePos.x, escapePos.z))
          pushEvent('fleeing', { from: danger.name || 'mob' })
        } else {
          isPerformingAction = false
          currentAction = 'idle'
        }
        break
      }

      case 'attack': {
        const targetMob = Object.values(bot.entities)
          .find(e => e.type === 'mob' && bot.entity.position.distanceTo(e.position) < 12)

        if (targetMob) {
          bot.pathfinder.setGoal(new goals.GoalFollow(targetMob, 2))
          
          // Attack loop while chasing
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
        // Simple search for nearby stone/ore blocks if nearby
        const block = bot.findBlock({
          matching: (b) => ['stone', 'coal_ore', 'iron_ore', 'dirt'].includes(b.name),
          maxDistance: 6
        })

        if (block) {
          bot.pathfinder.setGoal(new goals.GoalGetToBlock(block.position.x, block.position.y, block.position.z))
          await bot.dig(block)
          pushEvent('mined_block', { block: block.name })
        } else {
          pushEvent('mine_failed', {})
        }
        isPerformingAction = false
        currentAction = 'idle'
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
  if (!bot) return res.json({ connected: false })
  const nearby = Object.values(bot.entities)
    .filter(e => e !== bot.entity && bot.entity.position.distanceTo(e.position) < 16)
    .map(e => ({ name: e.name || e.username || e.type, dist: Math.round(bot.entity.position.distanceTo(e.position)) }))
    .slice(0, 8)

  res.json({
    connected: true,
    health: Math.round(bot.health),
    food: Math.round(bot.food),
    position: bot.entity.position,
    time: bot.time.timeOfDay > 13000 ? 'night' : 'day',
    nearby_players: nearby,
    current_action: currentAction,
    is_performing_action: isPerformingAction
  })
})

app.post('/action', async (req, res) => {
  const { action, params } = req.body
  const result = await performAction(action, params)
  res.json(result)
})

app.listen(BRIDGE_PORT, () => {
  console.log(`Mineflayer Express Bridge running on port ${BRIDGE_PORT}`)
})