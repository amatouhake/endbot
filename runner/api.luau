-- Trusted bootstrap; the exchange closure is removed from script globals below.
local exchange = _exchange
local emitLog = _log
_exchange = nil
_log = nil

local declared = false
local function request(message)
    local reply = exchange(message)
    if not reply.accepted then
        error(reply.error or "host rejected request", 3)
    end
    return reply
end

function params(schema)
    assert(not declared, "params must be called exactly once, before operations")
    assert(type(schema) == "table", "params expects a table")
    declared = true
    return request({type = "params", schema = schema}).args
end

local function ready()
    assert(declared, "declare params{} before running")
end

function wait(ticks)
    ready()
    assert(type(ticks) == "number" and ticks % 1 == 0 and ticks >= 0, "invalid wait ticks")
    request({type = "wait", ticks = ticks})
end

function log(text)
    ready()
    assert(type(text) == "string", "log expects text")
    emitLog(text)
end

function bot(name)
    ready()
    assert(type(name) == "string" and #name > 0 and #name <= 64, "invalid bot name")
    return table.freeze({
        move = function(self, direction, options)
            assert(direction == "forward" or direction == "back" or direction == "left" or direction == "right", "invalid direction")
            assert(type(options) == "table" and type(options.ticks) == "number" and options.ticks >= 1 and options.ticks % 1 == 0, "invalid move ticks")
            return request({type = "operation", bot = name, operation = "move", direction = direction, ticks = options.ticks})
        end,
        look = function(self, yaw, pitch)
            assert(type(yaw) == "number" and type(pitch) == "number", "invalid look angles")
            return request({type = "operation", bot = name, operation = "look", yaw = yaw, pitch = pitch})
        end,
    })
end
