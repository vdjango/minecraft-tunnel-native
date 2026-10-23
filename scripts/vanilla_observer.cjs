// Protocol observer only: the four players under test are full Mojang GUI clients.
const mineflayer = require('mineflayer')
const fs = require('fs')
const names = process.argv[4] ? JSON.parse(process.argv[4]) : ['GuiHost', 'GuiGuest1', 'GuiGuest2', 'GuiGuest3']
const observeSeconds = Number(process.argv[5] || 0)
const report = {started: new Date().toISOString(), operations: {}, movementUpdates: {}, passed: false}
report.serverReportedPlayerPingMs = []
const started = Date.now()
const bot = mineflayer.createBot({host: '127.0.0.1', port: Number(process.argv[2]),
  username: 'NativeObserver', auth: 'offline', version: '1.21.8'})
const save = () => fs.writeFileSync(process.argv[3], JSON.stringify(report, null, 2))
const positions = new Map()
bot.on('entityMoved', entity => {
  if (!names.includes(entity.username)) return
  const old = positions.get(entity.username)
  positions.set(entity.username, entity.position.clone())
  if (old) {
    const distance = entity.position.distanceTo(old)
    if (distance > .02 && distance < 1 && entity.position.z > old.z) {
      report.movementUpdates[entity.username] = (report.movementUpdates[entity.username] || 0) + 1
      save()
    }
  }
})
bot.on('spawn', async () => { await bot.waitForChunksToLoad(); console.log('Observer ready'); save() })
bot.on('playerUpdated', player => {
  if (names.includes(player.username) && Date.now() - started > 30000) {
    report.serverReportedPlayerPingMs.push({name: player.username, ping: player.ping, elapsedSeconds: (Date.now() - started) / 1000})
    save()
  }
})
bot.on('blockUpdate', (_, block) => {
  if (!block || block.position.y !== -60 || block.position.z !== 2) return
  const index = (block.position.x - 24) / 3
  if (!Number.isInteger(index) || index < 0 || index >= names.length) return
  const events = report.operations[names[index]] ||= []
  if (events.at(-1) !== block.name) { events.push(block.name); save(); console.log(names[index], events.join(' -> ')) }
  if (names.every(name => report.movementUpdates[name] >= 3 &&
    ['stone', 'air', 'dirt'].every((block, step) => report.operations[name]?.[step] === block))) {
    report.passed = true; report.completed = new Date().toISOString(); save(); if (!observeSeconds) bot.quit()
  }
})
bot.on('error', error => { report.failure = error.message; save(); process.exitCode = 1 })
if (observeSeconds) setTimeout(() => { save(); bot.quit() }, observeSeconds * 1000).unref()
setTimeout(() => { if (!report.passed) { report.failure = 'Real GUI operations timed out'; save(); process.exitCode = 1; bot.quit() } }, 240000).unref()
