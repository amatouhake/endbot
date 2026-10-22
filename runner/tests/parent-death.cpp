// Linux-only test helper: become the orphan runner's parent and reap its SIGKILL.
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>
#include <signal.h>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>

int main(int argc, char** argv)
{
    if (argc != 2 || prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0) return 1;
    std::string frame;
    if (!std::getline(std::cin, frame)) return 1;
    frame += '\n';
    int input[2];
    if (pipe(input) != 0) return 2;
    int identity[2];
    if (pipe(identity) != 0) return 2;
    pid_t creator = fork();
    if (creator == -1) return 3;
    if (creator == 0) {
        close(input[1]);
        close(identity[0]);
        int output[2];
        if (pipe(output) != 0) _exit(4);
        std::string parent = std::to_string(getpid());
        pid_t runner = fork();
        if (runner == -1) _exit(5);
        if (runner == 0) {
            close(identity[1]);
            close(output[0]);
            if (dup2(input[0], STDIN_FILENO) == -1) _exit(6);
            close(input[0]);
            if (dup2(output[1], STDOUT_FILENO) == -1) _exit(6);
            close(output[1]);
            execl(argv[1], argv[1], "--max-address-space", "268435456", "--max-cpu-seconds", "0",
                  "--parent-pid", parent.c_str(), "--nice", "10", static_cast<char*>(nullptr));
            _exit(7);
        }
        close(input[0]);
        close(output[1]);
        if (write(identity[1], &runner, sizeof(runner)) != sizeof(runner)) _exit(8);
        close(identity[1]);
        // params is emitted only after startup confinement and VM initialization.
        FILE* stream = fdopen(output[0], "r");
        char line[4096];
        bool ready = stream && fgets(line, sizeof(line), stream)
            && std::string(line).find("\"type\":\"params\"") != std::string::npos;
        if (stream) fclose(stream);
        _exit(ready ? 0 : 9);
    }
    close(input[0]);
    size_t offset = 0;
    while (offset < frame.size()) {
        ssize_t written = write(input[1], frame.data() + offset, frame.size() - offset);
        if (written <= 0) return 10;
        offset += size_t(written);
    }
    // Hold the writer open so the runner blocks awaiting a reply, rather than EOF.
    close(identity[1]);
    pid_t runner = -1;
    ssize_t count = read(identity[0], &runner, sizeof(runner));
    close(identity[0]);
    int creatorStatus = 0;
    if (waitpid(creator, &creatorStatus, 0) != creator) return 10;
    bool ready = WIFEXITED(creatorStatus) && WEXITSTATUS(creatorStatus) == 0;
    if (count != sizeof(runner) || runner <= 0) return 11;
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    do {
        int status = 0;
        pid_t reaped = waitpid(runner, &status, WNOHANG);
        if (reaped == runner) {
            if (!ready || !WIFSIGNALED(status) || WTERMSIG(status) != SIGKILL) return 12;
            puts("parent-death killed");
            return 0;
        }
        if (reaped == -1) return 13;
        usleep(10000);
    } while (std::chrono::steady_clock::now() < deadline);
    kill(runner, SIGKILL);
    waitpid(runner, nullptr, 0);
    return 14;
}
