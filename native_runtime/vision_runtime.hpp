#pragma once
#include "vision_runtime.h"
#include <stdexcept>
#include <string>
#include <utility>
namespace modu_vision {
struct Result { int status; std::string json; };
class Executor {
    void* handle_;
public:
    Executor(const std::string& package,const std::string& options="{}") {
        char* error=nullptr;handle_=mv_create(package.c_str(),options.c_str(),&error);
        if(!handle_){std::string message=error?error:"Cannot create executor";mv_free(error);throw std::runtime_error(message);}
    }
    Executor(const Executor&)=delete;Executor& operator=(const Executor&)=delete;
    ~Executor(){mv_release(handle_);}
    Result execute(const std::string& request){char* output=nullptr;int status=mv_execute(handle_,request.c_str(),&output);std::string json=output?output:"";mv_free(output);if(status==1)throw std::runtime_error(json);return {status,std::move(json)};}
    Result predict(const std::string& image,const char* id=nullptr){char* output=nullptr;int status=mv_predict(handle_,image.c_str(),id,&output);std::string json=output?output:"";mv_free(output);if(status==1)throw std::runtime_error(json);return {status,std::move(json)};}
    bool cancel(){int status=mv_cancel(handle_);if(status<0)throw std::runtime_error("Cancellation is unavailable for this executor");return status==1;}
};
using Predictor=Executor;
}
