$( document ).ready(function() {

	$(function() {
	  $("tr").each(function(){
	    var col_val4 = $(this).find("td:eq(3)").text();
	    var col_val5 = $(this).find("td:eq(4)").text();
	    var col_val6 = $(this).find("td:eq(5)").text();
	    if (col_val4 == "TAPS-Undo"){
	      $(this).addClass('blue-bg');  //the selected class colors the row blue//
	    }

	    if (col_val5 == "True" && col_val6 == "Success!"){
	      $(this).addClass('green-bg');  //the selected class colors the row green//
	    } else if (col_val5 == "False" && col_val6 != "Success!"){
	      	$(this).addClass('red-bg');  //the selected class colors the row red//
	    } else{
	    	$(this).addClass('grey-bg');  //the selected class colors the row grey//
	    }
	  });
	});


});