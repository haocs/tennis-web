// Stable app identity for Local Network permission; one independent worker role.
#import <AppKit/AppKit.h>
#import <Network/Network.h>
#include <signal.h>
static NSTask *child;
static nw_connection_t connection;
static dispatch_source_t termination;
static NSString *option(NSArray<NSString *> *args, NSString *key) {
    NSUInteger i = [args indexOfObject:key];
    return i != NSNotFound && i+1 < args.count ? args[i+1] : nil;
}
int main(int argc, const char **argv) {
    @autoreleasepool {
        NSArray *args = NSProcessInfo.processInfo.arguments;
        NSString *worker = option(args,@"--worker"), *config = option(args,@"--config"), *role = option(args,@"--role");
        if (!worker || !config || !role || ![@[@"render",@"ai"] containsObject:role]) return 2;
        NSDictionary *body = [NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:config] options:0 error:nil];
        NSURL *url = [NSURL URLWithString:body[@"server_url"]];
        if (!url.host) return 2;
        NSApplication *app = NSApplication.sharedApplication;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        child = [NSTask new]; child.executableURL = [NSURL fileURLWithPath:worker];
        child.arguments = @[@"--config",config,@"--role",role];
        child.terminationHandler = ^(NSTask *task) { exit(task.terminationStatus); };
        NSString *port = url.port ? url.port.stringValue : ([url.scheme isEqual:@"https"] ? @"443" : @"80");
        nw_endpoint_t endpoint = nw_endpoint_create_host(url.host.UTF8String,port.UTF8String);
        nw_parameters_t parameters = nw_parameters_create_secure_tcp(NW_PARAMETERS_DISABLE_PROTOCOL,NW_PARAMETERS_DEFAULT_CONFIGURATION);
        connection = nw_connection_create(endpoint,parameters);
        nw_connection_set_queue(connection,dispatch_get_main_queue());
        nw_connection_set_state_changed_handler(connection, ^(nw_connection_state_t state,nw_error_t error) {
            if (state == nw_connection_state_ready) {
                nw_connection_set_state_changed_handler(connection,nil); nw_connection_cancel(connection);
                NSError *failure = nil;
                if (![child launchAndReturnError:&failure]) { fprintf(stderr,"Worker launch failed: %s\n",failure.description.UTF8String);exit(1); }
            } else if (state == nw_connection_state_waiting) {
                fprintf(stderr,"Waiting for NAS network access (%d)\n",error ? nw_error_get_error_code(error) : 0);
            } else if (state == nw_connection_state_failed) { fprintf(stderr,"NAS connection failed\n");exit(1); }
        });
        signal(SIGTERM,SIG_IGN);
        termination = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL,SIGTERM,0,dispatch_get_main_queue());
        dispatch_source_set_event_handler(termination, ^{ if (child.running) [child terminate]; [app terminate:nil]; });
        dispatch_resume(termination);nw_connection_start(connection);[app run];
    }
    return 0;
}
