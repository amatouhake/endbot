local args = params {
    first = {type = "string", default = "Alice"},
    second = {type = "string", default = "Bob"},
    ticks = {type = "number", default = 2},
}
bot(args.first):move("forward", {ticks = args.ticks})
wait(1)
bot(args.second):move("left", {ticks = args.ticks})
wait(1)
bot(args.first):move("right", {ticks = args.ticks})
bot(args.second):look(90, 10)
wait(args.ticks)
log("two-bot sequence finished")
