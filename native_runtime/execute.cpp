#include "vision_runtime.hpp"
#include <fstream>
#include <iostream>
#include <sstream>
int main(int argc,char** argv){
    if(argc<3){std::cerr<<"Usage: vision_execute PACKAGE REQUEST_JSON [DEADLINE_MS]\n";return 2;}
    try{std::ifstream file(argv[2]);if(!file)throw std::runtime_error("Input JSON is unreadable");std::stringstream request;request<<file.rdbuf();
        std::string options=argc>3?"{\"deadline_ms\":"+std::to_string(std::stoll(argv[3]))+"}":"{}";
        modu_vision::Executor executor(argv[1],options);auto result=executor.execute(request.str());std::cout<<result.json<<"\n";return result.status==2?3:0;
    }catch(const std::exception& error){std::cerr<<error.what()<<"\n";return 2;}
}
