#!/usr/bin/env python3
"""Make Applesauce's dynarmic/oaknut allocate JIT memory itself when no
debugger is attached, instead of executing `brk #0xf00d` (fatal without one).

Run from the root of the Applesauce repo, after `git submodule update --init
--recursive`:

    python3 patch-oaknut-jit.py

Safe to run twice.
"""
import pathlib, sys

path = pathlib.Path("vendor/dynarmic/externals/oaknut/include/oaknut/code_block.hpp")
if not path.exists():
    sys.exit(f"{path} not found - run from the repo root with submodules checked out")
src = path.read_text()
if "AUTOJIT_PATCH" in src:
    sys.exit("already patched")

def sub(old, new):
    global src
    if src.count(old) != 1:
        sys.exit(f"anchor not found exactly once:\n{old[:80]}...")
    src = src.replace(old, new)

# 1. headers + helper that says whether a debugger is still attached.
sub("""#    include <sys/mman.h>
#    include <unistd.h>
#else""", """#    include <sys/mman.h>
#    include <sys/sysctl.h>
#    include <sys/types.h>
#    include <unistd.h>
#else""")

sub("""__attribute__((noinline, optnone, naked)) static void* prepare_jit_region""",
"""// AUTOJIT_PATCH: `brk #0xf00d` below is only serviceable while a debugger is
// still attached (StikDebug). TrollStore's "Enable JIT" attaches and then
// detaches, and `dynamic-codesigning` never attaches anything; in both cases
// the trap kills the process. P_TRACED tells the two situations apart.
inline bool debugger_is_attached()
{
    struct kinfo_proc info;
    info.kp_proc.p_flag = 0;
    size_t size = sizeof(info);
    int mib[4] = {CTL_KERN, KERN_PROC, KERN_PROC_PID, static_cast<int>(getpid())};
    if (sysctl(mib, 4, &info, &size, nullptr, 0) != 0)
        return false;
    return (info.kp_proc.p_flag & P_TRACED) != 0;
}

__attribute__((noinline, optnone, naked)) static void* prepare_jit_region""")

# 2. allocation: direct MAP_JIT mapping when nobody can service the trap.
sub("""        } else {
            m_memory = (std::uint32_t*)detail::prepare_jit_region(nullptr, size);""",
"""        } else if (!detail::debugger_is_attached()) {
            // No debugger: rely on CS_DEBUGGED (already set by TrollStore's
            // Enable JIT) or the dynamic-codesigning entitlement.
            void* region = mmap(nullptr, size, PROT_READ | PROT_WRITE | PROT_EXEC,
                                MAP_ANON | MAP_PRIVATE | MAP_JIT, -1, 0);
            if (region == MAP_FAILED)
                throw std::bad_alloc{};

            m_memory = static_cast<std::uint32_t*>(region);
            m_wmemory = m_memory;
            reusable_region = {m_memory, m_wmemory, size, true};
            // m_should_detach_jit_server stays false: nothing to detach from.
        } else {
            m_memory = (std::uint32_t*)detail::prepare_jit_region(nullptr, size);""")

path.write_text(src)
print("patched", path)
