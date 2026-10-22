local args = params {
    param.string("first", "Alice"),
    param.string("second", "Bob"),
    param.number("ticks", 2),
}
bot(args.first):move("forward", {ticks = args.ticks})
wait(1)
bot(args.second):move("left", {ticks = args.ticks})
wait(1)
bot(args.first):move("right", {ticks = args.ticks})
bot(args.second):look(90, 10)
wait(args.ticks)
log("two-bot sequence finished")
