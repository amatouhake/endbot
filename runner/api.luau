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
table.freeze(param)

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
        if not table.find({"group", "bot", "pos", "number", "boolean", "string"}, spec.type) then
            error("Unknown script parameter type", 2)
        end
        for key in spec do
            if key ~= "name" and key ~= "type" and key ~= "default" then
                error("Invalid script parameter declarations", 2)
            end
        end
        if spec.default ~= nil then
            local expected = if spec.type == "number" then "number" elseif spec.type == "boolean" then "boolean" else "string"
            if type(spec.default) ~= expected or (expected == "number" and (spec.default ~= spec.default or math.abs(spec.default) == math.huge)) then
                error("Invalid script parameter default", 2)
            end
        end
    end
    declared = true
    return request({type = "params", schema = schema}).args
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
local function valid(value, spec)
    local kind, t = spec.type, type(value)
    local number = t == "number" and value == value and math.abs(value) < math.huge
    local ok = (kind == "string" and t == "string") or (kind == "number" and number)
        or (kind == "integer" and number and value % 1 == 0) or (kind == "boolean" and t == "boolean")
        or (kind == "coordinate" and (number or t == "string"))
        or (kind == "stringOrInteger" and (t == "string" or (number and value % 1 == 0)))
        or (kind == "integerOrDefault" and (value == "default" or (number and value % 1 == 0)))
    if not ok then return false end
    if spec.choices and not table.find(spec.choices, value) then return false end
    return true
end
local function arguments(variants, ...)
    local input = table.pack(...)
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
                for key, value in input[count + 1] do
                    local spec = shape.options[key]
                    if not spec or not valid(value, spec) then ok = false; break end
                    named[key] = value
                end
            end
        end
        if ok then return named end
    end
    error("invalid typed operation arguments", 3)
end
local target
target = function(name, isGroup)
    ready()
    assert(type(name) == "string" and #name > 0 and #name <= 64, "invalid target")
    local handle = {}
    for operation, variants in definitions do
        handle[operation] = function(self, ...)
            local named = arguments(variants, ...)
            local reply = request({type = "operation", target = name, operation = operation, params = named})
            return reply.result
        end
    end
    handle.waitMining = function(self, options)
        if options == nil then options = {} end
        assert(type(options) == "table", "invalid waitMining options")
        for key in options do assert(key == "timeout", "invalid waitMining option") end
        local ticks = if options.timeout == nil then 1200 else options.timeout
        assert(type(ticks) == "number" and ticks % 1 == 0 and ticks >= 0 and ticks <= 2147483647, "invalid waitMining timeout")
        return request({type = "waitMining", target = name, timeout = ticks}).result
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
