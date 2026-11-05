-- Trusted bootstrap. Only the host supplies the operation grammar.
local exchange, emitLog, definitions = _exchange, _log, _operations
_exchange, _log, _operations = nil, nil, nil
local declared = false
local function request(message)
    local reply = exchange(message)
    if not reply.accepted then error(reply.error or "host rejected request", 3) end
    return reply
end

param = {}
for _, kind in {"group", "bot", "pos", "number", "boolean", "string"} do
    local name = if kind == "boolean" then "bool" else kind
    param[name] = function(parameterName, default)
        local descriptor = {name = parameterName, type = kind}
        if default ~= nil then descriptor.default = default end
        return table.freeze(descriptor)
    end
end
-- One of fixed strings: `param.choice("mode", {"once", "continuous"}, "once")`; forms show a dropdown.
param.choice = function(parameterName, choices, default)
    local descriptor = {name = parameterName, type = "choice", choices = choices}
    if type(choices) == "table" then descriptor.choices = table.freeze(table.clone(choices)) end
    if default ~= nil then descriptor.default = default end
    return table.freeze(descriptor)
end
table.freeze(param)
local KINDS = {"group", "bot", "pos", "number", "boolean", "string", "choice"}
-- Capabilities beyond the basic set: `requires { "observe.chat" }`, once, before params{}.
-- The host checks the names; this only fixes the shape and the order.
local required = nil
function requires(names)
    if declared then error("declare requires before params", 2) end
    if required ~= nil then error("declare requires exactly once", 2) end
    if type(names) ~= "table" then error("requires expects a list of capability names", 2) end
    local list, seen = {}, {}
    for key, name in names do
        if type(key) ~= "number" or key % 1 ~= 0 or key < 1 or key > #names or type(name) ~= "string"
            or #name < 1 or #name > 64 or seen[name] or #list >= 32 then
            error("requires expects a list of capability names", 2)
        end
        seen[name] = true
        table.insert(list, name)
    end
    if #list ~= #names then error("requires expects a list of capability names", 2) end
    required = table.freeze(list)
end

function params(schema)
    if declared then error("declare params exactly once", 2) end
    if type(schema) ~= "table" then error("Script parameters must be an array", 2) end
    local count, names = 0, {}
    for key in schema do
        if type(key) ~= "number" or key % 1 ~= 0 or key < 1 or key > #schema then
            error("Script parameters must be an array", 2)
        end
        count += 1
    end
    if count ~= #schema then error("Script parameters must be an array", 2) end
    if count > 32 then error("Invalid script parameter declarations", 2) end
    for _, spec in schema do
        if type(spec) ~= "table" then error("Invalid script parameter declarations", 2) end
        local name = spec.name
        if type(name) ~= "string" or #name > 32 or not string.match(name, "^[A-Za-z_][A-Za-z0-9_]*$") then
            error("Invalid script parameter name", 2)
        end
        if names[name] then error("Duplicate script parameter name", 2) end
        names[name] = true
        if not table.find(KINDS, spec.type) then
            error("Unknown script parameter type", 2)
        end
        for key in spec do
            if key ~= "name" and key ~= "type" and key ~= "default" and not (key == "choices" and spec.type == "choice") then
                error("Invalid script parameter declarations", 2)
            end
        end
        if spec.type == "choice" then
            local choices = spec.choices
            if type(choices) ~= "table" or #choices < 1 or #choices > 32 then
                error("Invalid script parameter declarations", 2)
            end
            local seen, n = {}, 0
            for key, choice in choices do
                n += 1
                if type(key) ~= "number" or type(choice) ~= "string" or #choice < 1 or #choice > 64 or seen[choice] then
                    error("Invalid script parameter declarations", 2)
                end
                seen[choice] = true
            end
            if n ~= #choices then error("Invalid script parameter declarations", 2) end
        end
        if spec.default ~= nil then
            local expected = if spec.type == "number" then "number" elseif spec.type == "boolean" then "boolean" else "string"
            if type(spec.default) ~= expected or (expected == "number" and (spec.default ~= spec.default or math.abs(spec.default) == math.huge))
                or (spec.type == "choice" and not table.find(spec.choices, spec.default)) then
                error("Invalid script parameter default", 2)
            end
        end
    end
    declared = true
    local args = request({type = "params", schema = schema, requires = required or {}}).args
    -- Positions arrive as {x, y, z}; vectors add and scale (`a.origin + vector.create(2, 0, 0)`).
    for _, spec in schema do
        local value = args[spec.name]
        if spec.type == "pos" and type(value) == "table" then
            args[spec.name] = vector.create(value.x, value.y, value.z)
        end
    end
    return args
end
local function ready() assert(declared, "declare params{} before running") end
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
-- Areas (BOT-027): values built only here, so a script cannot forge one. Each is an empty frozen
-- table; its clause text ("within 6 except ~ ~-1 ~") stays private and is what the host parses.
local AREAS = setmetatable({}, {__mode = "k"})
local AreaMethods = {}
local AreaMeta = table.freeze({__index = AreaMethods})
local function areaNumber(value, what)
    if type(value) == "string" and string.match(value, "^~%-?%d*%.?%d*$") then return value end
    assert(type(value) == "number" and value == value and math.abs(value) <= 30000000, "invalid area " .. what)
    if value % 1 == 0 then return string.format("%d", value) end
    return (string.gsub(string.format("%.6f", value), "%.?0+$", ""))
end
-- Coordinates as numbers or "~" strings, or a vector for three of them.
local function areaPoints(count, ...)
    local values = {}
    for index = 1, select("#", ...) do
        local value = select(index, ...)
        if type(value) == "vector" then
            table.insert(values, areaNumber(value.x, "coordinate"))
            table.insert(values, areaNumber(value.y, "coordinate"))
            table.insert(values, areaNumber(value.z, "coordinate"))
        else
            table.insert(values, areaNumber(value, "coordinate"))
        end
    end
    assert(#values == count, "an area needs " .. count .. " coordinates")
    return table.concat(values, " ")
end
local function newArea(base, excepts)
    local value = setmetatable({}, AreaMeta)
    AREAS[value] = {base = base, excepts = excepts}
    return table.freeze(value)
end
function AreaMethods.minus(self, other)
    local own, removed = AREAS[self], AREAS[other]
    assert(own, "minus is called on an area: a:minus(b)")
    assert(removed and #removed.excepts == 0, "minus takes an area built with area.cell, area.box or area.sphere")
    local excepts = table.clone(own.excepts)
    table.insert(excepts, removed.base)
    return newArea(own.base, table.freeze(excepts))
end
table.freeze(AreaMethods)
local function areaText(value)
    local entry = AREAS[value]
    local text = "within " .. entry.base
    for _, base in entry.excepts do text ..= " except " .. base end
    return text
end
area = table.freeze({
    cell = function(...) return newArea(areaPoints(3, ...), table.freeze({})) end,
    box = function(...) return newArea(areaPoints(6, ...), table.freeze({})) end,
    sphere = function(radius, ...)
        assert(type(radius) == "number" and radius > 0, "invalid area radius")
        local base = areaNumber(radius, "radius")
        if select("#", ...) > 0 then base ..= " of " .. areaPoints(3, ...) end
        return newArea(base, table.freeze({}))
    end,
})
local function valid(value, spec)
    local kind, t = spec.type, type(value)
    local number = t == "number" and value == value and math.abs(value) < math.huge
    local ok = ((kind == "string" or kind == "player") and t == "string") or (kind == "number" and number)
        or (kind == "integer" and number and value % 1 == 0) or (kind == "boolean" and t == "boolean")
        or (kind == "coordinate" and (number or t == "string"))
        or (kind == "stringOrInteger" and (t == "string" or (number and value % 1 == 0)))
        or (kind == "integerOrDefault" and (value == "default" or (number and value % 1 == 0)))
        or (kind == "area" and AREAS[value] ~= nil)
    if not ok then return false end
    if spec.choices and not table.find(spec.choices, value) then return false end
    return true
end
local function arguments(variants, operation, ...)
    -- A vector stands for three coordinates: `bot:tp(a.origin)` is `bot:tp(x, y, z)`.
    local input = {n = 0}
    for index = 1, select("#", ...) do
        local value = select(index, ...)
        if type(value) == "vector" then
            input[input.n + 1], input[input.n + 2], input[input.n + 3] = value.x, value.y, value.z
            input.n += 3
        else
            input.n += 1
            input[input.n] = value
        end
    end
    for _, shape in variants do
        local count, named = #shape.positional, {}
        local ok = input.n == count or (input.n == count + 1 and type(input[count + 1]) == "table")
        if ok then
            for index, pair in shape.positional do
                local key, spec = pair[1], pair[2]
                if not valid(input[index], spec) then ok = false; break end
                named[key] = input[index]
            end
            if ok and input.n > count then
                if AREAS[input[count + 1]] then error("pass an area as an option: {area = a}", 3) end
                for key, value in input[count + 1] do
                    local spec = shape.options[key]
                    if not spec or not valid(value, spec) then ok = false; break end
                    named[key] = if spec.type == "area" then areaText(value) else value
                end
            end
        end
        if ok then return named end
    end
    if operation == "blocks" and (input.n >= 4 or (type(input[input.n]) == "table" and input[input.n].radius ~= nil)) then
        error("blocks(type, x, y, z) and {radius = r} were replaced by areas: "
            .. "blocks(type, {area = area.sphere(r, x, y, z)})", 3)
    end
    error("invalid typed operation arguments", 3)
end
-- Every wait takes `{timeout = ticks}` (default 1200) and returns {ok, code, reason, ...}; a timeout is
-- ok = false, code = "timeout" and does not stop the work it waited for.
local function timeout(options, what, default)
    if options == nil then options = {} end
    assert(type(options) == "table", "invalid " .. what .. " options")
    for key in options do assert(key == "timeout", "invalid " .. what .. " option") end
    local ticks = if options.timeout == nil then default else options.timeout
    assert(ticks == nil or (type(ticks) == "number" and ticks % 1 == 0 and ticks >= 0 and ticks <= 2147483647),
        "invalid " .. what .. " timeout")
    return ticks
end
local target
target = function(name, isGroup)
    ready()
    assert(type(name) == "string" and #name > 0 and #name <= 64, "invalid target")
    local handle = {}
    for operation, variants in definitions do
        handle[operation] = function(self, ...)
            local named = arguments(variants, operation, ...)
            local reply = request({type = "operation", target = name, operation = operation, params = named})
            local result = reply.result
            -- A continuing operation's result carries its job: `b:move("forward", {ticks = 40}).job:wait{}`.
            if type(result) == "table" and type(result.job) == "table" then
                local descriptor = result.job
                result.job = table.freeze({wait = function(_, options)
                    local ticks = timeout(options, "job wait", 1200)
                    return request({type = "waitJob", target = name, job = descriptor, timeout = ticks}).result
                end})
            end
            return result
        end
    end
    handle.waitMining = function(self, options)
        local ticks = timeout(options, "waitMining", 1200)
        return request({type = "waitMining", target = name, timeout = ticks}).result
    end
    handle.waitOnline = function(self, options)
        ready()
        local ticks = timeout(options, "waitOnline", nil)
        return request({type = "waitOnline", target = name, timeout = ticks}).result
    end
    if isGroup then
        handle.members = function(self)
            local reply = request({type = "members", target = name})
            local members = {}
            for i = 1, reply.count do members[i] = target(name .. "[" .. i .. "]", false) end
            return members
        end
    end
    handle._target = name
    return table.freeze(handle)
end
function bot(name) return target(name, false) end
function group(name)
    assert(type(name) == "string" and string.sub(name, 1, 1) == "#", "group requires #name")
    return target(name, true)
end
function waitOnline(value)
    ready()
    local name = if type(value) == "string" then value else value._target
    return request({type = "waitOnline", target = name}).result
end
