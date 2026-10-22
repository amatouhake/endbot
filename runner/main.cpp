#include "lua.h"
#include "lualib.h"
#include "Luau/Compiler.h"
#include "json.hpp"

#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;
constexpr size_t MaxFrame = 256 * 1024;
struct Run {
    size_t memory = 0, memoryLimit = 32 * 1024 * 1024;
    int nextId = 0, limitMs = 100, budget = 100000, remaining = 100000;
    Clock::time_point deadline;
    Json pending;
};

static void send(const Json& event) { std::cout << event.dump() << '\n' << std::flush; }
static Json readFrame()
{
    std::string line;
    char c;
    while (std::cin.get(c)) {
        if (c == '\n') return Json::parse(line);
        if (line.size() >= MaxFrame) throw std::runtime_error("IPC frame too large");
        line += c;
    }
    throw std::runtime_error("host EOF");
}
static void* allocate(void* ud, void* pointer, size_t oldSize, size_t size)
{
    auto& run = *static_cast<Run*>(ud);
    if (!pointer) oldSize = 0;
    if (!size) { run.memory -= oldSize; std::free(pointer); return nullptr; }
    if (size > run.memoryLimit - (run.memory - oldSize)) return nullptr;
    void* next = std::realloc(pointer, size);
    if (next) run.memory = run.memory - oldSize + size;
    return next;
}
static Run& context(lua_State* L) { return *static_cast<Run*>(lua_callbacks(L)->userdata); }
static void interrupt(lua_State* L, int gc)
{
    if (gc >= 0) return;
    auto& run = context(L);
    if (--run.remaining <= 0 || Clock::now() >= run.deadline)
        luaL_error(L, "execution budget exceeded");
}
static Json fromLua(lua_State* L, int index, int depth = 0, bool array = false)
{
    if (depth > 8) throw std::runtime_error("API value too deep or cyclic");
    switch (lua_type(L, index)) {
    case LUA_TNIL: return nullptr;
    case LUA_TBOOLEAN: return bool(lua_toboolean(L, index));
    case LUA_TNUMBER: {
        double number = lua_tonumber(L, index);
        if (!std::isfinite(number)) throw std::runtime_error("non-finite API number");
        return number;
    }
    case LUA_TSTRING: {
        size_t length; const char* text = lua_tolstring(L, index, &length);
        if (length > 65536) throw std::runtime_error("API string too large");
        return std::string(text, length);
    }
    case LUA_TTABLE: {
        Json result = array ? Json::array() : Json::object();
        index = lua_absindex(L, index);
        if (array) {
            int length = lua_objlen(L, index), count = 0;
            lua_pushnil(L);
            while (lua_next(L, index)) {
                double key = lua_type(L, -2) == LUA_TNUMBER ? lua_tonumber(L, -2) : 0;
                if (key < 1 || key > length || key != std::floor(key))
                    throw std::runtime_error("Script parameters must be an array");
                ++count;
                lua_pop(L, 1);
            }
            if (count != length || length > 32) throw std::runtime_error("Invalid script parameter declarations");
            for (int i = 1; i <= length; ++i) {
                lua_rawgeti(L, index, i);
                result.push_back(fromLua(L, -1, depth + 1));
                lua_pop(L, 1);
            }
            return result;
        }
        lua_pushnil(L);
        while (lua_next(L, index)) {
            if (lua_type(L, -2) != LUA_TSTRING) throw std::runtime_error("API object keys must be strings");
            if (result.size() >= 128) throw std::runtime_error("API object too large");
            std::string key = lua_tostring(L, -2);
            result[key] = fromLua(L, -1, depth + 1, depth == 0 && key == "schema");
            lua_pop(L, 1);
        }
        return result;
    }
    default: throw std::runtime_error("unsupported API value");
    }
}
static void toLua(lua_State* L, const Json& value, int depth = 0)
{
    if (depth > 8) throw std::runtime_error("reply too deep");
    if (value.is_null()) lua_pushnil(L);
    else if (value.is_boolean()) lua_pushboolean(L, value.get<bool>());
    else if (value.is_number()) lua_pushnumber(L, value.get<double>());
    else if (value.is_string()) {
        const auto& s = value.get_ref<const std::string&>(); lua_pushlstring(L, s.data(), s.size());
    } else if (value.is_object()) {
        lua_newtable(L);
        for (auto it = value.begin(); it != value.end(); ++it) {
            toLua(L, it.value(), depth + 1); lua_setfield(L, -2, it.key().c_str());
        }
    } else if (value.is_array()) {
        lua_newtable(L);
        int i = 1;
        for (const auto& entry : value) { toLua(L, entry, depth + 1); lua_rawseti(L, -2, i++); }
    } else throw std::runtime_error("unsupported JSON argument");
}
static int exchange(lua_State* L)
{
    // Luau is built with C++ exceptions: local JSON/string destructors run on VM errors.
    auto& run = context(L);
    try {
        run.pending = fromLua(L, 1);
        run.pending["id"] = ++run.nextId;
        if (run.pending.dump().size() > MaxFrame) throw std::runtime_error("API frame too large");
    } catch (const std::exception& e) { luaL_error(L, "%s", e.what()); }
    return lua_yield(L, 0);
}
static int logText(lua_State* L)
{
    size_t length; const char* value = luaL_checklstring(L, 1, &length);
    if (length > 4096) luaL_error(L, "log text too large");
    send({{"type", "log"}, {"text", std::string(value, length)}});
    return 0;
}
static int load(lua_State* L, const std::string& source, const char* name)
{
    std::string code = Luau::compile(source);
    return luau_load(L, name, code.data(), code.size(), 0);
}
int main()
{
    try {
        Json input = readFrame();
        if (input.at("type") != "load") throw std::runtime_error("expected load");
        Run run;
        Json limits = input.value("limits", Json::object());
        run.limitMs = limits.value("sliceMs", 100);
        run.budget = limits.value("interruptBudget", 100000);
        run.remaining = run.budget;
        run.memoryLimit = limits.value("memoryBytes", 32 * 1024 * 1024);
        if (run.limitMs < 1 || run.budget < 1 || run.memoryLimit < 1024 * 1024)
            throw std::runtime_error("invalid limits");
        std::string source = input.at("source"), bootstrap = input.at("bootstrap");
        size_t sourceLimit = limits.value("maxSourceBytes", size_t(65536));
        if (sourceLimit > 65536 || source.size() > sourceLimit || bootstrap.size() > 16384)
            throw std::runtime_error("source too large");
        std::unique_ptr<lua_State, decltype(&lua_close)> state(lua_newstate(allocate, &run), lua_close);
        if (!state) throw std::runtime_error("cannot allocate VM");
        lua_State* L = state.get();
        lua_callbacks(L)->userdata = &run;
        lua_callbacks(L)->interrupt = interrupt;
        run.deadline = Clock::now() + std::chrono::milliseconds(run.limitMs);
        luaL_openlibs(L);
        for (const char* name : {"io", "os", "require", "package", "debug", "loadstring", "getfenv", "setfenv", "print", "coroutine"}) {
            lua_pushnil(L); lua_setglobal(L, name);
        }
        lua_pushcfunction(L, exchange, "exchange"); lua_setglobal(L, "_exchange");
        lua_pushcfunction(L, logText, "log"); lua_setglobal(L, "_log");
        toLua(L, input.value("operations", Json::object())); lua_setglobal(L, "_operations");
        if (load(L, bootstrap, "=api") || lua_pcall(L, 0, 0, 0))
            throw std::runtime_error(lua_tostring(L, -1));
        luaL_sandbox(L);
        lua_State* script = lua_newthread(L);
        luaL_sandboxthread(script);
        if (load(script, source, "=script")) {
            send({{"type", "error"}, {"phase", "compile"}, {"message", lua_tostring(script, -1)}}); return 0;
        }
        int arguments = 0;
        for (;;) {
            run.deadline = Clock::now() + std::chrono::milliseconds(run.limitMs);
            int status = lua_resume(script, nullptr, arguments);
            if (status == LUA_OK) { send({{"type", "done"}}); return 0; }
            if (status != LUA_YIELD) {
                send({{"type", "error"}, {"phase", "execute"}, {"message", lua_tostring(script, -1)}}); return 0;
            }
            Json pending = std::move(run.pending);
            if (!pending.is_object()) throw std::runtime_error("unexpected script yield");
            send(pending);
            Json reply = readFrame();
            if (reply.at("type") == "stop") {
                send({{"type", "stopped"}, {"id", reply.at("id")}}); return 0;
            }
            if (reply.at("type") != "reply" || reply.at("id") != pending.at("id"))
                throw std::runtime_error("unexpected or stale reply id");
            lua_settop(script, 0);
            toLua(script, reply);
            arguments = 1;
        }
    } catch (const std::exception& e) {
        send({{"type", "error"}, {"phase", "protocol"}, {"message", e.what()}}); return 1;
    }
}
