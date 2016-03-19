angular.module('app', [
    // external libs

    'ngRoute',
    'ngMessages',
    'satellizer',

    'ngResource',
    'ngSanitize',
    'ngMaterial',

    'templates.app',  // this is how it accesses the cached templates in ti.js

    'staticPages',

    'badgeDefs',
    'personPage',
    'settingsPage',
    'badgePage',
    'aboutPages',

    'numFormat'

]);




angular.module('app').config(function ($routeProvider,
                                       $authProvider,
                                       $locationProvider) {


    $locationProvider.html5Mode(true);

    // handle 404s by redirecting to landing page.
    $routeProvider.otherwise({ redirectTo: '/' })



    $authProvider.oauth2({
        name: "orcid",
        url: "/api/auth/orcid",
        clientId: "APP-PF0PDMP7P297AU8S",
        redirectUri: window.location.origin, // + "/logging-you-in",
        authorizationEndpoint: "https://orcid.org/oauth/authorize",

        defaultUrlParams: ['response_type', 'client_id', 'redirect_uri'],
        requiredUrlParams: ['scope', 'show_login'],
        scope: ['/authenticate'],
        responseType: 'code',
        showLogin: 'true',
        responseParams: {
            code: 'code',
            clientId: 'clientId',
            redirectUri: 'redirectUri'
        }
    });
});


angular.module('app').run(function($route,
                                   $rootScope,
                                   $timeout,
                                   $location) {





    (function(i,s,o,g,r,a,m){i['GoogleAnalyticsObject']=r;i[r]=i[r]||function(){
            (i[r].q=i[r].q||[]).push(arguments)},i[r].l=1*new Date();a=s.createElement(o),
        m=s.getElementsByTagName(o)[0];a.async=1;a.src=g;m.parentNode.insertBefore(a,m)
    })(window,document,'script','//www.google-analytics.com/analytics.js','ga');

    ga('create', 'UA-23384030-3', 'auto');


    $rootScope.$on('$routeChangeStart', function(next, current){
    })
    $rootScope.$on('$routeChangeSuccess', function(next, current){
        window.scrollTo(0, 0)
        ga('send', 'pageview', { page: $location.url() });

    })
    $rootScope.$on('$routeChangeError', function(event, current, previous, rejection){
        console.log("$routeChangeError")
        $location.path("/")
        window.scrollTo(0, 0)
    });


    // from http://cwestblog.com/2012/09/28/javascript-number-getordinalfor/
    (function(o) {
        Number.getOrdinalFor = function(intNum, includeNumber) {
            return (includeNumber ? intNum : "")
                + (o[((intNum = Math.abs(intNum % 100)) - 20) % 10] || o[intNum] || "th");
        };
    })([,"st","nd","rd"]);




    /*
     this lets you change the args of the URL without reloading the whole view. from
     - https://github.com/angular/angular.js/issues/1699#issuecomment-59283973
     - http://joelsaupe.com/programming/angularjs-change-path-without-reloading/
     - https://github.com/angular/angular.js/issues/1699#issuecomment-60532290
     */
    var original = $location.path;
    $location.path = function (path, reload) {
        if (reload === false) {
            var lastRoute = $route.current;
            var un = $rootScope.$on('$locationChangeSuccess', function () {
                $route.current = lastRoute;
                un();
            });
            $timeout(un, 500)
        }
        return original.apply($location, [path]);
    };




});


angular.module('app').controller('AppCtrl', function(
    $rootScope,
    $scope,
    $location,
    NumFormat,
    $auth,
    $http,
    $mdDialog,
    $sce){

    $scope.auth = $auth
    $scope.numFormat = NumFormat
    $scope.moment = moment // this will break unless moment.js loads over network...

    $scope.global = {}
    $scope.global.isLandingPage = false

    $rootScope.$on('$routeChangeStart', function(next, current){
        $scope.global.isLandingPage = false
    })

    $scope.trustHtml = function(str){
        console.log("trusting html:", str)
        return $sce.trustAsHtml(str)
    }


    // used in the nav bar, also for signup on the landing page.
    $scope.authenticate = function () {
        console.log("authenticate!")

        $auth.authenticate("orcid")
            .then(function(resp){
                var orcid_id = $auth.getPayload()['sub']
                console.log("you have successfully logged in!", resp, $auth.getPayload())

                // take the user to their profile.
                $location.path("/u/" + orcid_id)

            })
            .catch(function(error){
                console.log("there was an error logging in:", error)
            })
    }

    var showAlert = function(msgText, titleText, okText){
        if (!okText){
            okText = "ok"
        }
          $mdDialog.show(
                  $mdDialog.alert()
                    .clickOutsideToClose(true)
                    .title(titleText)
                    .textContent(msgText)
                    .ok(okText)
            );
    }









    /********************************************************
     *
     *  stripe stuff
     *
    ********************************************************/



    var stripeInfo = {
        email: null,
        tokenId: null,
        cents: 0,

        // optional
        fullName: null,
        orcidId: null
    }

    var stripeHandler = StripeCheckout.configure({
        key: stripePublishableKey,
        locale: 'auto',
        token: function(token) {
            stripeInfo.email = token.email
            stripeInfo.tokenId = token.id

            console.log("now we are doing things with the user's info", stripeInfo)
            $http.post("/api/donation", stripeInfo)
                .success(function(resp){
                    console.log("the credit card charge worked!", resp)
                    showAlert(
                        "We appreciate your donation, and we've emailed you a receipt.",
                        "Thanks so much!"
                    )
                })
                .error(function(resp){
                    console.log("error!", resp.message)
                    if (resp.message){
                        var reason = resp.message
                    }
                    else {
                        var reason = "Sorry, we had a server error! Drop us a line at team@impactstory.org and we'll fix it."
                    }
                    showAlert(reason, "Credit card error")
                })
        }
      });
    $scope.donate = function(cents){
        console.log("donate", cents)
        stripeInfo.cents = cents
        var me = $auth.getPayload() // this might break on the donate page.
        if (me){
            stripeInfo.fullName = me.given_names + " " + me.family_name
            stripeInfo.orcidId = me.sub
        }

        stripeHandler.open({
          name: 'Impactstory donation',
          description: "We're a US 501(c)3",
          amount: cents
        });
    }


});

