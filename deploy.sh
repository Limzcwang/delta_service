#!/bin/bash

# Delta Service OpenCloud 一键部署脚本
# 支持开发、测试、生产环境的自动化部署

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 显示帮助信息
show_help() {
    cat << EOF
Delta Service OpenCloud 部署脚本

用法: $0 [选项] [环境]

选项:
    -h, --help          显示帮助信息
    -b, --build         构建Docker镜像
    -g, --generate      生成部署配置
    -d, --deploy        执行部署
    -t, --test          运行测试
    -c, --clean         清理部署
    -a, --all           执行完整部署流程
    -v, --verbose       详细输出
    --dry-run          模拟运行，不实际执行

环境:
    development         开发环境
    test                测试环境
    production          生产环境

示例:
    $0 -a production     完整部署到生产环境
    $0 -b -g test       构建镜像并生成测试环境配置
    $0 --dry-run -a     模拟完整部署流程

EOF
}

# 检查依赖
check_dependencies() {
    log_info "检查依赖工具..."
    
    local missing_deps=()
    
    # 检查必需工具
    for cmd in docker kubectl python3; do
        if ! command -v $cmd &> /dev/null; then
            missing_deps+=("$cmd")
        fi
    done
    
    if [ ${#missing_deps[@]} -ne 0 ]; then
        log_error "缺少必需工具: ${missing_deps[*]}"
        exit 1
    fi
    
    log_success "所有依赖工具已安装"
}

# 检查OpenCloud连接
check_opencloud() {
    log_info "检查OpenCloud连接..."
    
    if ! kubectl cluster-info &> /dev/null; then
        log_error "无法连接到OpenCloud集群"
        log_error "请检查kubeconfig配置"
        exit 1
    fi
    
    log_success "OpenCloud连接正常"
}

# 构建Docker镜像
build_docker_image() {
    local environment=$1
    local version=$2
    
    log_info "构建Docker镜像 (环境: $environment, 版本: $version)..."
    
    if [ "$DRY_RUN" = true ]; then
        log_warning "模拟运行: docker build -t delta-service:$version ."
        return 0
    fi
    
    if docker build -t delta-service:$version .; then
        log_success "Docker镜像构建成功"
    else
        log_error "Docker镜像构建失败"
        exit 1
    fi
}

# 生成部署配置
generate_deployment_config() {
    local environment=$1
    
    log_info "生成部署配置 (环境: $environment)..."
    
    if [ "$DRY_RUN" = true ]; then
        log_warning "模拟运行: python generate_deployment.py $environment -o opencloud-$environment.yaml"
        return 0
    fi
    
    if python generate_deployment.py $environment -o opencloud-$environment.yaml; then
        log_success "部署配置生成成功: opencloud-$environment.yaml"
    else
        log_error "部署配置生成失败"
        exit 1
    fi
}

# 执行部署
deploy_to_opencloud() {
    local environment=$1
    
    log_info "部署到OpenCloud (环境: $environment)..."
    
    if [ "$DRY_RUN" = true ]; then
        log_warning "模拟运行: kubectl apply -f opencloud-$environment.yaml"
        return 0
    fi
    
    if kubectl apply -f opencloud-$environment.yaml; then
        log_success "部署命令执行成功"
    else
        log_error "部署命令执行失败"
        exit 1
    fi
}

# 等待部署完成
wait_for_deployment() {
    local environment=$1
    local namespace=$2
    local timeout=300  # 5分钟超时
    local interval=10  # 每10秒检查一次
    
    log_info "等待部署完成 (超时: ${timeout}秒)..."
    
    local start_time=$(date +%s)
    local elapsed=0
    
    while [ $elapsed -lt $timeout ]; do
        # 检查Pod状态
        local pod_status=$(kubectl get pods -n $namespace -l app=delta-service -o jsonpath='{.items[*].status.phase}' 2>/dev/null || echo "")
        
        if echo "$pod_status" | grep -q "Running"; then
            log_success "所有Pod已启动并运行"
            return 0
        fi
        
        sleep $interval
        elapsed=$(($(date +%s) - start_time))
        
        if [ $((elapsed % 30)) -eq 0 ]; then
            log_info "等待中... (已等待 ${elapsed}秒)"
        fi
    done
    
    log_error "部署超时，Pod未在${timeout}秒内启动"
    return 1
}

# 运行测试
run_tests() {
    log_info "运行测试..."
    
    if [ "$DRY_RUN" = true ]; then
        log_warning "模拟运行: python test_error_handling.py"
        log_warning "模拟运行: python test_logging.py"
        return 0
    fi
    
    # 运行错误处理测试
    if python test_error_handling.py; then
        log_success "错误处理测试通过"
    else
        log_error "错误处理测试失败"
        exit 1
    fi
    
    # 运行日志系统测试
    if python test_logging.py; then
        log_success "日志系统测试通过"
    else
        log_error "日志系统测试失败"
        exit 1
    fi
}

# 验证部署
verify_deployment() {
    local environment=$1
    local namespace=$2
    
    log_info "验证部署..."
    
    # 检查Pod状态
    log_info "检查Pod状态:"
    kubectl get pods -n $namespace -l app=delta-service
    
    # 检查服务状态
    log_info "检查服务状态:"
    kubectl get services -n $namespace -l app=delta-service
    
    # 检查部署状态
    log_info "检查部署状态:"
    kubectl get deployments -n $namespace -l app=delta-service
    
    # 检查日志
    log_info "检查Pod日志:"
    kubectl logs -n $namespace -l app=delta-service --tail=10
    
    log_success "部署验证完成"
}

# 清理部署
clean_deployment() {
    local environment=$1
    local namespace=$2
    
    log_info "清理部署 (环境: $environment)..."
    
    if [ "$DRY_RUN" = true ]; then
        log_warning "模拟运行: kubectl delete -f opencloud-$environment.yaml"
        return 0
    fi
    
    if kubectl delete -f opencloud-$environment.yaml; then
        log_success "部署清理成功"
    else
        log_error "部署清理失败"
        exit 1
    fi
}

# 获取配置信息
get_config() {
    local environment=$1
    
    # 从config.yaml读取配置
    local namespace=$(python3 -c "import yaml; config=yaml.safe_load(open('config.yaml')); print(config['namespaces']['$environment'])" 2>/dev/null || echo "delta-$environment")
    local version=$(python3 -c "import yaml; config=yaml.safe_load(open('config.yaml')); print(config['base']['version'])" 2>/dev/null || echo "latest")
    
    echo "$namespace $version"
}

# 主函数
main() {
    local BUILD=false
    local GENERATE=false
    local DEPLOY=false
    local TEST=false
    local CLEAN=false
    local ALL=false
    local VERBOSE=false
    DRY_RUN=false
    
    local ENVIRONMENT=""
    
    # 解析参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_help
                exit 0
                ;;
            -b|--build)
                BUILD=true
                shift
                ;;
            -g|--generate)
                GENERATE=true
                shift
                ;;
            -d|--deploy)
                DEPLOY=true
                shift
                ;;
            -t|--test)
                TEST=true
                shift
                ;;
            -c|--clean)
                CLEAN=true
                shift
                ;;
            -a|--all)
                ALL=true
                shift
                ;;
            -v|--verbose)
                VERBOSE=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            development|test|production)
                ENVIRONMENT=$1
                shift
                ;;
            *)
                log_error "未知参数: $1"
                show_help
                exit 1
                ;;
        esac
    done
    
    # 设置详细输出
    if [ "$VERBOSE" = true ]; then
        set -x
    fi
    
    # 检查环境参数
    if [ -z "$ENVIRONMENT" ]; then
        log_error "请指定部署环境 (development, test, production)"
        show_help
        exit 1
    fi
    
    # 如果选择全部，设置所有标志
    if [ "$ALL" = true ]; then
        BUILD=true
        GENERATE=true
        DEPLOY=true
        TEST=true
    fi
    
    # 如果没有选择任何操作，显示帮助
    if [ "$BUILD" = false ] && [ "$GENERATE" = false ] && [ "$DEPLOY" = false ] && [ "$TEST" = false ] && [ "$CLEAN" = false ]; then
        log_warning "未指定任何操作，显示帮助信息"
        show_help
        exit 0
    fi
    
    # 显示部署信息
    log_info "=== Delta Service OpenCloud 部署 ==="
    log_info "环境: $ENVIRONMENT"
    log_info "操作: $( [ "$BUILD" = true ] && echo "构建镜像 " )$( [ "$GENERATE" = true ] && echo "生成配置 " )$( [ "$DEPLOY" = true ] && echo "部署 " )$( [ "$TEST" = true ] && echo "测试 " )$( [ "$CLEAN" = true ] && echo "清理 " )"
    log_info "模式: $( [ "$DRY_RUN" = true ] && echo "模拟运行" || echo "实际执行" )"
    echo
    
    # 获取配置信息
    read -r NAMESPACE VERSION <<< "$(get_config $ENVIRONMENT)"
    
    # 检查依赖
    check_dependencies
    
    # 检查OpenCloud连接（仅在需要时）
    if [ "$DEPLOY" = true ] || [ "$CLEAN" = true ] || [ "$ALL" = true ]; then
        check_opencloud
    fi
    
    # 运行测试
    if [ "$TEST" = true ]; then
        run_tests
    fi
    
    # 构建镜像
    if [ "$BUILD" = true ]; then
        build_docker_image $ENVIRONMENT $VERSION
    fi
    
    # 生成配置
    if [ "$GENERATE" = true ]; then
        generate_deployment_config $ENVIRONMENT
    fi
    
    # 清理部署
    if [ "$CLEAN" = true ]; then
        clean_deployment $ENVIRONMENT $NAMESPACE
        exit 0
    fi
    
    # 执行部署
    if [ "$DEPLOY" = true ]; then
        deploy_to_opencloud $ENVIRONMENT
        
        # 等待部署完成
        if [ "$DRY_RUN" = false ]; then
            wait_for_deployment $ENVIRONMENT $NAMESPACE
        fi
        
        # 验证部署
        verify_deployment $ENVIRONMENT $NAMESPACE
    fi
    
    # 显示完成信息
    echo
    log_success "=== 部署流程完成 ==="
    
    if [ "$DEPLOY" = true ] && [ "$DRY_RUN" = false ]; then
        log_info "服务访问信息:"
        log_info "- API文档: http://<external-ip>/docs"
        log_info "- WebSocket: ws://<external-ip>:8765"
        log_info "- 命名空间: $NAMESPACE"
        
        echo
        log_info "监控命令:"
        log_info "kubectl get pods -n $NAMESPACE"
        log_info "kubectl get services -n $NAMESPACE"
        log_info "kubectl logs -n $NAMESPACE -l app=delta-service"
    fi
}

# 执行主函数
main "$@"