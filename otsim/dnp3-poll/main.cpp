// mine-dnp3-poll: one-shot DNP3 class 0 (integrity) read; prints JSON and exits.
// Read-only replacement for the upstream pydnp3 test master (pydnp3 does not
// build on Debian Bookworm). Startup integrity/unsolicited/restart-IIN writes
// are all disabled so the only request sent is a class 0 READ.
// usage: mine-dnp3-poll <host> [port=20000] [outstation=1024] [master=1] [timeout_s=10]
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <map>
#include <mutex>
#include <thread>

#include "opendnp3/ConsoleLogger.h"
#include "opendnp3/DNP3Manager.h"
#include "opendnp3/master/DefaultMasterApplication.h"
#include "opendnp3/master/ISOEHandler.h"

using namespace opendnp3;

class Collect : public ISOEHandler {
public:
  std::mutex mu;
  std::map<int, bool> bi, bo;
  std::map<int, double> ai, ao;
  bool done = false;
  void Process(const HeaderInfo&, const ICollection<Indexed<Binary>>& v) override {
    std::scoped_lock l(mu); v.ForeachItem([&](const Indexed<Binary>& x) { bi[x.index] = x.value.value; });
  }
  void Process(const HeaderInfo&, const ICollection<Indexed<BinaryOutputStatus>>& v) override {
    std::scoped_lock l(mu); v.ForeachItem([&](const Indexed<BinaryOutputStatus>& x) { bo[x.index] = x.value.value; });
  }
  void Process(const HeaderInfo&, const ICollection<Indexed<Analog>>& v) override {
    std::scoped_lock l(mu); v.ForeachItem([&](const Indexed<Analog>& x) { ai[x.index] = x.value.value; });
  }
  void Process(const HeaderInfo&, const ICollection<Indexed<AnalogOutputStatus>>& v) override {
    std::scoped_lock l(mu); v.ForeachItem([&](const Indexed<AnalogOutputStatus>& x) { ao[x.index] = x.value.value; });
  }
  void Process(const HeaderInfo&, const ICollection<Indexed<DoubleBitBinary>>&) override {}
  void Process(const HeaderInfo&, const ICollection<Indexed<Counter>>&) override {}
  void Process(const HeaderInfo&, const ICollection<Indexed<FrozenCounter>>&) override {}
  void Process(const HeaderInfo&, const ICollection<Indexed<OctetString>>&) override {}
  void Process(const HeaderInfo&, const ICollection<Indexed<TimeAndInterval>>&) override {}
  void Process(const HeaderInfo&, const ICollection<Indexed<BinaryCommandEvent>>&) override {}
  void Process(const HeaderInfo&, const ICollection<Indexed<AnalogCommandEvent>>&) override {}
  void Process(const HeaderInfo&, const ICollection<DNPTime>&) override {}
  void BeginFragment(const ResponseInfo&) override {}
  void EndFragment(const ResponseInfo& info) override { std::scoped_lock l(mu); if (info.fir || info.fin) done = info.fin; }
};

template <class M> static void dump(const char* name, const M& m, bool last) {
  std::cout << "\"" << name << "\":{";
  bool first = true;
  for (auto& [k, v] : m) { std::cout << (first ? "" : ",") << "\"" << k << "\":" << v; first = false; }
  std::cout << "}" << (last ? "" : ",");
}

int main(int argc, char** argv) {
  if (argc < 2) { std::cerr << "usage: mine-dnp3-poll <host> [port] [outstation] [master] [timeout_s]\n"; return 2; }
  std::string host = argv[1];
  uint16_t port = argc > 2 ? std::atoi(argv[2]) : 20000;
  uint16_t outst = argc > 3 ? std::atoi(argv[3]) : 1024;
  uint16_t mst = argc > 4 ? std::atoi(argv[4]) : 1;
  int timeout = argc > 5 ? std::atoi(argv[5]) : 10;

  DNP3Manager manager(1, ConsoleLogger::Create());
  auto channel = manager.AddTCPClient("poll", levels::NOTHING, ChannelRetry::Default(),
                                      {IPEndpoint(host, port)}, "0.0.0.0", nullptr);
  MasterStackConfig cfg;
  cfg.master.disableUnsolOnStartup = false;  // do not send DISABLE_UNSOLICITED
  cfg.master.ignoreRestartIIN = true;        // do not WRITE to clear the restart IIN
  cfg.master.startupIntegrityClassMask = ClassField::None();
  cfg.master.unsolClassMask = ClassField::None();
  cfg.master.integrityOnEventOverflowIIN = false;
  cfg.link.LocalAddr = mst;
  cfg.link.RemoteAddr = outst;
  auto handler = std::make_shared<Collect>();
  auto master = channel->AddMaster("poll", handler, DefaultMasterApplication::Create(), cfg);
  master->Enable();
  // Re-issue the class 0 read every 2 s until data arrives: a read requested before the
  // TCP channel/link is up can be dropped, especially right after the outstation starts.
  auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(timeout);
  auto next_scan = std::chrono::steady_clock::now();
  while (std::chrono::steady_clock::now() < deadline) {
    { std::scoped_lock l(handler->mu); if (handler->done && (handler->bi.size() + handler->ai.size()) > 0) break; }
    if (std::chrono::steady_clock::now() >= next_scan) {
      master->ScanClasses(ClassField(ClassField::CLASS_0), handler);
      next_scan = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
  }
  std::scoped_lock l(handler->mu);
  bool ok = (handler->bi.size() + handler->ai.size() + handler->bo.size() + handler->ao.size()) > 0;
  std::cout << "{\"host\":\"" << host << "\",\"port\":" << port << ",\"ok\":" << (ok ? "true" : "false") << ",";
  dump("binary_inputs", handler->bi, false);
  dump("binary_outputs", handler->bo, false);
  dump("analog_inputs", handler->ai, false);
  dump("analog_outputs", handler->ao, true);
  std::cout << "}" << std::endl;
  master->Disable();
  return ok ? 0 : 1;
}
