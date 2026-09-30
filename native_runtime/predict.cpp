#include "vision_runtime.hpp"
#include <iostream>
int main(int argc,char** argv){
    if(argc<3){std::cerr<<"Usage: vision_predict PACKAGE IMAGE [DEADLINE_MS]\n";return 2;}
    try{
        std::string options=argc>3?"{\"deadline_ms\":"+std::to_string(std::stoll(argv[3]))+"}":"{}";
        modu_vision::Predictor predictor(argv[1],options);
        auto result=predictor.predict(argv[2]);std::cout<<result.json<<"\n";return result.status==2?3:0;
    }catch(const std::exception& error){std::cerr<<error.what()<<"\n";return 2;}
}
