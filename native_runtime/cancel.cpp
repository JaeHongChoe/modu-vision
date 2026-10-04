#include "vision_runtime.hpp"
#include <chrono>
#include <future>
#include <iostream>
#include <thread>

int main(int argc,char** argv){
    if(argc!=3){std::cerr<<"Usage: vision_cancel_demo PACKAGE IMAGE\n";return 2;}
    try{
        modu_vision::Executor executor(argv[1],"{\"deadline_ms\":30000}");
        auto running=std::async(std::launch::async,[&](){return executor.predict(argv[2]);});
        auto limit=std::chrono::steady_clock::now()+std::chrono::seconds(10);
        bool requested=false;
        while(std::chrono::steady_clock::now()<limit){
            if(executor.cancel()){requested=true;break;}
            if(running.wait_for(std::chrono::milliseconds(0))==std::future_status::ready)break;
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        auto cancelled=running.get();
        if(!requested||cancelled.status!=3)throw std::runtime_error("Inference completed before confirmed cancellation");
        if(executor.cancel())throw std::runtime_error("Idle executor retained active cancellation");
        auto next=executor.predict(argv[2]);
        std::cout<<"{\"cancelled\":"<<cancelled.json<<",\"next\":"<<next.json<<"}\n";
        return next.status==0?0:2;
    }catch(const std::exception& error){std::cerr<<error.what()<<"\n";return 2;}
}
