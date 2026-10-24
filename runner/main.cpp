#include "lua.h"
#include "lualib.h"
#include "Luau/Compiler.h"
#include "json.hpp"

#include <chrono>
#include <charconv>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>

#ifdef __linux__
#include <csignal>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <unistd.h>
#endif

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

static void confinementError(const char* message)
{
    // Error reporting must not need a JSON/string allocation after RLIMIT_AS.
    std::fputs("{\"type\":\"error\",\"phase\":\"confinement\",\"message\":\"confinement failed: ", stdout);
    for (const unsigned char* p = reinterpret_cast<const unsigned char*>(message); *p; ++p) {
        if (*p == '"' || *p == '\\') { std::fputc('\\', stdout); std::fputc(*p, stdout); }
        else if (*p < 0x20 || *p >= 0x7f) std::fprintf(stdout, "\\u%04x", unsigned(*p));
        else std::fputc(*p, stdout);
    }
    std::fputs("\"}\n", stdout);
    std::fflush(stdout);
}

struct Confinement {
    uint64_t memory = 0, cpu = 0, parent = 0, priority = 0;
    bool enabled = false;
};

static Confinement arguments(int argc, char** argv)
{
    Confinement result;
    unsigned seen = 0;
    for (int i = 1; i < argc; ++i) {
        std::string flag = argv[i];
        uint64_t* target;
        uint64_t maximum;
        unsigned bit;
        if (flag == "--max-address-space") {
            target = &result.memory; maximum = std::numeric_limits<uint64_t>::max() - 1; bit = 1;
        } else if (flag == "--max-cpu-seconds") {
            target = &result.cpu; maximum = std::numeric_limits<int>::max(); bit = 2;
        } else if (flag == "--parent-pid") {
            target = &result.parent; maximum = std::numeric_limits<int>::max(); bit = 4;
        } else if (flag == "--nice") {
            target = &result.priority; maximum = 10; bit = 8;
        } else throw std::runtime_error("unknown argument: " + flag);
        if (seen & bit) throw std::runtime_error("duplicate argument: " + flag);
        if (++i == argc) throw std::runtime_error("missing value for " + flag);
        std::string value = argv[i];
        auto parsed = std::from_chars(value.data(), value.data() + value.size(), *target);
        if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos
            || parsed.ec != std::errc() || parsed.ptr != value.data() + value.size() || *target > maximum
            || (bit == 4 && *target == 0) || (bit == 8 && *target != 10))
            throw std::runtime_error("invalid value for " + flag);
        seen |= bit;
    }
    if (seen != 0 && seen != 15) throw std::runtime_error("all four confinement arguments are required together");
    result.enabled = seen != 0;
    return result;
}

#ifdef __linux__
static void systemFailure(const char* call)
{
    throw std::runtime_error(std::string(call) + ": " + std::strerror(errno));
}

static void limitResource(decltype(RLIMIT_AS) resource, uint64_t soft, uint64_t hard)
{
    if (hard >= uint64_t(RLIM_INFINITY)) throw std::runtime_error("rlimit value out of range");
    rlimit limit;
    if (getrlimit(resource, &limit) != 0) systemFailure("getrlimit");
    // Never relax a stricter inherited soft or hard limit.
    if (limit.rlim_cur == RLIM_INFINITY || soft < limit.rlim_cur) limit.rlim_cur = rlim_t(soft);
    if (limit.rlim_max == RLIM_INFINITY || hard < limit.rlim_max) limit.rlim_max = rlim_t(hard);
    if (limit.rlim_cur > limit.rlim_max) limit.rlim_cur = limit.rlim_max;
    if (setrlimit(resource, &limit) != 0) systemFailure("setrlimit");
}
#endif

static void confine(const Confinement& limits)
{
#ifdef __linux__
    if (!limits.enabled) return;
    if (limits.memory) limitResource(RLIMIT_AS, limits.memory, limits.memory);
    // SIGXCPU at the soft cap, SIGKILL one second later if ignored.
    if (limits.cpu) limitResource(RLIMIT_CPU, limits.cpu, limits.cpu + 1);
    if (prctl(PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0) != 0) systemFailure("PR_SET_PDEATHSIG");
    if (getppid() != pid_t(limits.parent)) throw std::runtime_error("parent PID changed");
    errno = 0;
    if (nice(int(limits.priority)) == -1 && errno != 0) systemFailure("nice");
    // RLIMIT_NPROC is per UID, not per run; deliberately leave it unchanged.
#else
    (void)limits; // Windows uses the plugin's Job Object, not runner-side limits.
#endif
}

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
        if (run.pending.dump().size() >= MaxFrame) throw std::runtime_error("API frame too large");
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
int main(int argc, char** argv)
{
    try {
        confine(arguments(argc, argv));
    } catch (const std::exception& e) {
        confinementError(e.what());
        return 1;
    }
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
            run.remaining = run.budget;
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
